"""Academic English-to-Chinese translation with provider failover."""

from __future__ import annotations

import html
import logging
import re
import threading
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

import requests


logger = logging.getLogger(__name__)


class TranslationError(RuntimeError):
    """Raised when every configured translation provider fails."""


@dataclass(frozen=True)
class TranslationResult:
    text: str
    provider: str


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return ''.join(self.parts)


class TranslationService:
    """Translate academic text through Azure, DeepSeek, then MyMemory."""

    # Longest phrases are replaced first so that shorter entries cannot split them.
    SOURCE_TERMS: Tuple[Tuple[str, str], ...] = (
        ('retrieval-augmented generation', '检索增强生成'),
        ('large language models', '大语言模型'),
        ('large language model', '大语言模型'),
        ('multi-agent systems', '多智能体系统'),
        ('multi-agent system', '多智能体系统'),
        ('memory substrate', '记忆底座'),
        ('memory consolidation', '记忆巩固'),
        ('vulnerability repair', '漏洞修复'),
        ('reinforcement learning', '强化学习'),
        ('chain-of-thought', '思维链'),
        ('chain of thought', '思维链'),
        ('prompt engineering', '提示工程'),
        ('smart contracts', '智能合约'),
        ('smart contract', '智能合约'),
        ('long-term memory', '长期记忆'),
        ('working memory', '工作记忆'),
        ('program repair', '程序修复'),
        ('agent memories', '智能体记忆'),
        ('agent memory', '智能体记忆'),
        ('AI agents', 'AI 智能体'),
        ('AI agent', 'AI 智能体'),
        ('long-horizon', '长程'),
        ('fine-tuning', '微调'),
        ('hallucination', '幻觉'),
    )

    POST_CORRECTIONS: Tuple[Tuple[str, str], ...] = (
        ('智能代理记忆', '智能体记忆'),
        ('代理内存', '智能体记忆'),
        ('代理记忆', '智能体记忆'),
        ('多代理系统', '多智能体系统'),
        ('多智能代理系统', '多智能体系统'),
        ('大型语言模型', '大语言模型'),
        ('检索增强型生成', '检索增强生成'),
        ('检索增强的生成', '检索增强生成'),
        ('思想链', '思维链'),
        ('工作内存', '工作记忆'),
        ('记忆整合', '记忆巩固'),
        ('AI 代理', 'AI 智能体'),
        ('内存底材', '记忆底座'),
        ('记忆基质', '记忆底座'),
        ('长期视野推理', '长程推理'),
        ('长时域推理', '长程推理'),
    )

    def __init__(
        self,
        azure_key: Optional[str] = None,
        azure_endpoint: str = 'https://api.cognitive.microsofttranslator.com',
        azure_region: Optional[str] = None,
        deepseek_key: Optional[str] = None,
        deepseek_base_url: Optional[str] = None,
        deepseek_model: Optional[str] = None,
        timeout: int = 20,
    ) -> None:
        self.azure_key = azure_key
        self.azure_endpoint = azure_endpoint.rstrip('/')
        self.azure_region = azure_region
        self.deepseek_key = deepseek_key
        self.deepseek_base_url = (deepseek_base_url or '').rstrip('/')
        self.deepseek_model = deepseek_model
        self.timeout = timeout
        self._deepseek_client = None
        self._client_lock = threading.Lock()

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'TranslationService':
        return cls(
            azure_key=config.get('AZURE_TRANSLATE_API_KEY'),
            azure_endpoint=config.get(
                'AZURE_TRANSLATE_ENDPOINT',
                'https://api.cognitive.microsofttranslator.com',
            ),
            azure_region=config.get('AZURE_TRANSLATE_REGION'),
            deepseek_key=config.get('DEEPSEEK_API_KEY'),
            deepseek_base_url=config.get('CUSTOM_DEEPSEEK_BASE_URL'),
            deepseek_model=config.get('CUSTOM_DEEPSEEK_MODEL'),
            timeout=config.get('TRANSLATION_TIMEOUT', 20),
        )

    @staticmethod
    def _term_pattern(source: str) -> re.Pattern[str]:
        return re.compile(
            rf'(?<![A-Za-z0-9]){re.escape(source)}(?![A-Za-z0-9])',
            re.IGNORECASE,
        )

    @classmethod
    def preprocess_terms(cls, text: str) -> str:
        """Replace known English terms with their preferred Chinese forms."""
        processed = text
        for source, target in cls.SOURCE_TERMS:
            processed = cls._term_pattern(source).sub(target, processed)
        return processed

    @classmethod
    def _prepare_azure_html(cls, text: str) -> str:
        """Escape user text and apply Azure's dynamic-dictionary markup."""
        processed = html.escape(text)
        for source, target in cls.SOURCE_TERMS:
            def dictionary_entry(match: re.Match[str], target_term: str = target) -> str:
                return (
                    '<mstrans:dictionary '
                    f'translation="{html.escape(target_term, quote=True)}">'
                    f'{match.group(0)}</mstrans:dictionary>'
                )

            processed = cls._term_pattern(source).sub(dictionary_entry, processed)
        return processed

    @classmethod
    def correct_terms(cls, text: str) -> str:
        corrected = text.strip()
        for source, target in cls.POST_CORRECTIONS:
            corrected = corrected.replace(source, target)
        # Azure may preserve spaces around glossary markup boundaries.
        corrected = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', corrected)
        return corrected

    def translate(self, text: str) -> TranslationResult:
        source_text = (text or '').strip()
        if not source_text:
            raise TranslationError('待翻译文本不能为空')

        prepared_text = self.preprocess_terms(source_text)
        failures: List[str] = []

        providers = (
            ('azure', lambda: self._translate_azure(source_text), bool(self.azure_key)),
            (
                'deepseek',
                lambda: self._translate_deepseek(prepared_text),
                bool(self.deepseek_key and self.deepseek_base_url and self.deepseek_model),
            ),
            ('mymemory', lambda: self._translate_mymemory(prepared_text), True),
        )

        for provider, operation, enabled in providers:
            if not enabled:
                logger.info('Skipping unconfigured translation provider: %s', provider)
                continue
            try:
                translated = self.correct_terms(operation())
                if not translated:
                    raise TranslationError('provider returned empty text')
                logger.info('Translation completed with provider=%s', provider)
                return TranslationResult(text=translated, provider=provider)
            except Exception as exc:
                failures.append(f'{provider}: {exc}')
                logger.warning('Translation provider %s failed: %s', provider, exc)

        raise TranslationError('All translation providers failed: ' + ' | '.join(failures))

    def _translate_azure(self, text: str) -> str:
        headers = {
            'Ocp-Apim-Subscription-Key': self.azure_key,
            'Content-Type': 'application/json; charset=UTF-8',
        }
        if self.azure_region:
            headers['Ocp-Apim-Subscription-Region'] = self.azure_region

        response = requests.post(
            f'{self.azure_endpoint}/translate',
            params={
                'api-version': '3.0',
                'from': 'en',
                'to': 'zh-Hans',
                'textType': 'html',
            },
            headers=headers,
            json=[{'Text': self._prepare_azure_html(text)}],
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        translated = payload[0]['translations'][0]['text']

        parser = _HTMLTextExtractor()
        parser.feed(translated)
        return parser.text()

    def _get_deepseek_client(self):
        if self._deepseek_client is None:
            with self._client_lock:
                if self._deepseek_client is None:
                    try:
                        import openai
                    except ImportError as exc:
                        raise TranslationError(
                            'DeepSeek fallback requires the openai package'
                        ) from exc
                    self._deepseek_client = openai.OpenAI(
                        api_key=self.deepseek_key,
                        base_url=self.deepseek_base_url,
                        timeout=self.timeout,
                    )
        return self._deepseek_client

    def _translate_deepseek(self, text: str) -> str:
        glossary = '\n'.join(
            f'- {source}: {target}' for source, target in self.SOURCE_TERMS
        )
        response = self._get_deepseek_client().chat.completions.create(
            model=self.deepseek_model,
            messages=[
                {
                    'role': 'system',
                    'content': (
                        '你是专业的英文学术论文翻译。将用户文本翻译为简体中文；'
                        '只输出译文，不解释。保留公式、代码、缩写和已有中文术语。'
                        '严格采用以下术语表：\n' + glossary
                    ),
                },
                {'role': 'user', 'content': text},
            ],
            temperature=0,
            max_tokens=max(256, min(4096, len(text) * 2)),
            extra_body={'thinking': {'type': 'disabled'}},
        )
        content = response.choices[0].message.content
        if not content:
            raise TranslationError('DeepSeek returned empty text')
        return content.strip()

    @staticmethod
    def _split_for_mymemory(text: str, limit: int = 450) -> List[str]:
        chunks: List[str] = []
        remaining = text.strip()
        while len(remaining) > limit:
            window = remaining[:limit]
            punctuation = max(window.rfind(mark) for mark in ('. ', '! ', '? ', '。', '！', '？'))
            whitespace = window.rfind(' ')
            cut = punctuation + 1 if punctuation >= limit // 2 else whitespace
            if cut < limit // 2:
                cut = limit
            chunks.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _translate_mymemory(self, text: str) -> str:
        translated_chunks: List[str] = []
        for chunk in self._split_for_mymemory(text):
            response = requests.get(
                'https://api.mymemory.translated.net/get',
                params={'q': chunk, 'langpair': 'en|zh-CN'},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            translated = (payload.get('responseData') or {}).get('translatedText')
            if payload.get('responseStatus') != 200 or not translated:
                raise TranslationError(payload.get('responseDetails') or 'MyMemory error')
            translated_chunks.append(translated.strip())
        return ' '.join(translated_chunks)
