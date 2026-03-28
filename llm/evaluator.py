"""
LLM Evaluator for Paper Relevance Scoring
Supports OpenAI, Anthropic, and Zhipu AI (GLM) APIs
"""
import logging
import time
import os
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class LLMEvaluatorError(Exception):
    """Base exception for LLM Evaluator errors"""
    pass


class RateLimitError(LLMEvaluatorError):
    """Raised when API rate limit is exceeded"""
    pass


@dataclass
class EvalResult:
    """Result of paper evaluation"""
    score: int
    model: str
    provider: str
    raw_response: Optional[str] = None
    error: Optional[str] = None


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers"""

    def __init__(self, api_key: str, delay: float = 1.0):
        self.api_key = api_key
        self.delay = delay
        self._last_call_time = 0

    def _wait_for_rate_limit(self):
        """Wait to respect rate limiting"""
        elapsed = time.time() - self._last_call_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_call_time = time.time()

    @abstractmethod
    def evaluate(self, title: str, abstract: str, system_prompt: str) -> EvalResult:
        """Evaluate paper relevance"""
        pass


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider"""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", delay: float = 1.0):
        super().__init__(api_key, delay)
        self.model = model
        self._client = None

    def _get_client(self):
        """Lazy import and initialization of OpenAI client"""
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(api_key=self.api_key)
            except ImportError:
                raise LLMEvaluatorError("OpenAI package not installed. Install with: pip install openai")
        return self._client

    def evaluate(self, title: str, abstract: str, system_prompt: str) -> EvalResult:
        """Evaluate using OpenAI API"""
        self._wait_for_rate_limit()

        user_prompt = f"Title: {title}\n\nAbstract: {abstract}"

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0,
                max_tokens=10
            )

            content = response.choices[0].message.content.strip()
            score = self._parse_score(content)

            return EvalResult(
                score=score,
                model=self.model,
                provider="openai",
                raw_response=content
            )

        except ImportError as e:
            raise LLMEvaluatorError(f"OpenAI import failed: {e}")
        except Exception as e:
            error_msg = str(e)
            if "rate_limit" in error_msg.lower() or "429" in error_msg:
                raise RateLimitError(f"OpenAI rate limit exceeded: {e}")
            raise LLMEvaluatorError(f"OpenAI API error: {e}")

    def _parse_score(self, content: str) -> int:
        """Parse score from LLM response"""
        # Extract integer from response
        import re
        match = re.search(r'\b(\d{1,3})\b', content)
        if match:
            score = int(match.group(1))
            return max(0, min(100, score))  # Clamp to 0-100
        logger.warning(f"Could not parse score from response: {content}")
        return 0


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude API provider"""

    def __init__(self, api_key: str, model: str = "claude-3-5-haiku-20241022", delay: float = 1.0):
        super().__init__(api_key, delay)
        self.model = model
        self._client = None

    def _get_client(self):
        """Lazy import and initialization of Anthropic client"""
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                raise LLMEvaluatorError("Anthropic package not installed. Install with: pip install anthropic")
        return self._client

    def evaluate(self, title: str, abstract: str, system_prompt: str) -> EvalResult:
        """Evaluate using Anthropic API"""
        self._wait_for_rate_limit()

        user_prompt = f"Title: {title}\n\nAbstract: {abstract}"

        try:
            client = self._get_client()
            response = client.messages.create(
                model=self.model,
                max_tokens=10,
                temperature=0,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )

            content = response.content[0].text.strip()
            score = self._parse_score(content)

            return EvalResult(
                score=score,
                model=self.model,
                provider="anthropic",
                raw_response=content
            )

        except ImportError as e:
            raise LLMEvaluatorError(f"Anthropic import failed: {e}")
        except Exception as e:
            error_msg = str(e)
            if "rate_limit" in error_msg.lower() or "429" in error_msg:
                raise RateLimitError(f"Anthropic rate limit exceeded: {e}")
            raise LLMEvaluatorError(f"Anthropic API error: {e}")

    def _parse_score(self, content: str) -> int:
        """Parse score from LLM response"""
        import re
        match = re.search(r'\b(\d{1,3})\b', content)
        if match:
            score = int(match.group(1))
            return max(0, min(100, score))
        logger.warning(f"Could not parse score from response: {content}")
        return 0


class ZhipuProvider(BaseLLMProvider):
    """Zhipu AI (GLM) API provider"""

    def __init__(self, api_key: str, model: str = "glm-4-flash", delay: float = 1.0):
        super().__init__(api_key, delay)
        self.model = model
        self._client = None

    def _get_client(self):
        """Lazy import and initialization of Zhipu client"""
        if self._client is None:
            try:
                from zhipuai import ZhipuAI
                self._client = ZhipuAI(api_key=self.api_key)
            except ImportError:
                raise LLMEvaluatorError("ZhipuAI package not installed. Install with: pip install zhipuai")
        return self._client

    def evaluate(self, title: str, abstract: str, system_prompt: str) -> EvalResult:
        """Evaluate using Zhipu AI API"""
        self._wait_for_rate_limit()

        user_prompt = f"Title: {title}\n\nAbstract: {abstract}"

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0,
                max_tokens=10
            )

            content = response.choices[0].message.content.strip()
            score = self._parse_score(content)

            return EvalResult(
                score=score,
                model=self.model,
                provider="zhipu",
                raw_response=content
            )

        except ImportError as e:
            raise LLMEvaluatorError(f"ZhipuAI import failed: {e}")
        except Exception as e:
            error_msg = str(e)
            if "rate_limit" in error_msg.lower() or "429" in error_msg:
                raise RateLimitError(f"Zhipu AI rate limit exceeded: {e}")
            raise LLMEvaluatorError(f"Zhipu AI API error: {e}")

    def _parse_score(self, content: str) -> int:
        """Parse score from LLM response"""
        import re
        match = re.search(r'\b(\d{1,3})\b', content)
        if match:
            score = int(match.group(1))
            return max(0, min(100, score))
        logger.warning(f"Could not parse score from response: {content}")
        return 0


class CustomOpenAIProvider(BaseLLMProvider):
    """
    Custom OpenAI-compatible API provider

    Supports any OpenAI-compatible API endpoint with configurable:
    - Base URL (e.g., https://api.deepseek.com, https://api.moonshot.cn)
    - API key
    - Model name
    """

    def __init__(self, api_key: str, base_url: str, model: str = "gpt-4o-mini", delay: float = 1.0):
        super().__init__(api_key, delay)
        self.base_url = base_url.rstrip('/')
        self.model = model
        self._client = None

    def _get_client(self):
        """Lazy import and initialization of OpenAI client with custom base URL"""
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url
                )
            except ImportError:
                raise LLMEvaluatorError("OpenAI package not installed. Install with: pip install openai")
        return self._client

    def evaluate(self, title: str, abstract: str, system_prompt: str) -> EvalResult:
        """Evaluate using custom OpenAI-compatible API"""
        self._wait_for_rate_limit()

        user_prompt = f"Title: {title}\n\nAbstract: {abstract}"

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0,
                max_tokens=10
            )

            content = response.choices[0].message.content.strip()
            score = self._parse_score(content)

            return EvalResult(
                score=score,
                model=self.model,
                provider=f"custom ({self.base_url})",
                raw_response=content
            )

        except ImportError as e:
            raise LLMEvaluatorError(f"OpenAI import failed: {e}")
        except Exception as e:
            error_msg = str(e)
            if "rate_limit" in error_msg.lower() or "429" in error_msg:
                raise RateLimitError(f"Custom API rate limit exceeded: {e}")
            raise LLMEvaluatorError(f"Custom API error: {e}")

    def _parse_score(self, content: str) -> int:
        """Parse score from LLM response"""
        import re
        match = re.search(r'\b(\d{1,3})\b', content)
        if match:
            score = int(match.group(1))
            return max(0, min(100, score))
        logger.warning(f"Could not parse score from response: {content}")
        return 0


class LLMEvaluator:
    """
    Main LLM Evaluator class

    Evaluates paper relevance using configured LLM provider.

    Usage:
        evaluator = LLMEvaluator(provider='openai', api_key='...')
        result = evaluator.evaluate(title, abstract)
        if result.score >= threshold:
            # Save paper

    Supported providers:
        - openai: OpenAI (GPT-4o-mini, etc.)
        - anthropic: Anthropic (Claude)
        - zhipu: Zhipu AI (GLM)
        - custom: Any OpenAI-compatible API (set CUSTOM_LLM_BASE_URL)
    """

    DEFAULT_SYSTEM_PROMPT = (
        "You are an academic research assistant. Evaluate the relevance of the following paper "
        "to this specific research context: Automated smart contract vulnerability repair using "
        "multi-agent systems, LLM-based software engineering, and the analysis of real-world DeFi "
        "exploit incidents for benchmark datasets. Score the relevance from 0 to 100. Return ONLY "
        "the integer score."
    )

    def __init__(
        self,
        provider: str = "openai",
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        delay: float = 1.0,
        system_prompt: Optional[str] = None,
        enabled: bool = True
    ):
        """
        Initialize LLM Evaluator

        Args:
            provider: LLM provider ('openai', 'anthropic', 'zhipu', 'custom')
            api_key: API key (if None, reads from environment variable)
            model: Model name (if None, uses default for provider)
            delay: Delay between requests in seconds
            system_prompt: Custom system prompt (if None, uses default)
            enabled: Whether evaluation is enabled

        For custom provider, also set:
            CUSTOM_LLM_BASE_URL: Base URL of the API endpoint
            CUSTOM_LLM_MODEL: Model name (optional, has default)
        """
        self.enabled = enabled
        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT

        if not enabled:
            logger.info("LLM Evaluator is disabled")
            self._provider = None
            return

        # Get API key from parameter or environment
        if api_key is None:
            env_var_map = {
                'openai': 'OPENAI_API_KEY',
                'anthropic': 'ANTHROPIC_API_KEY',
                'zhipu': 'ZHIPUAI_API_KEY',
                'custom': 'CUSTOM_LLM_API_KEY'
            }
            api_key = os.environ.get(env_var_map.get(provider, ''))
            if not api_key:
                raise LLMEvaluatorError(
                    f"API key not provided. Set {env_var_map.get(provider)} environment variable "
                    f"or pass api_key parameter."
                )

        # Initialize provider
        provider_classes = {
            'openai': OpenAIProvider,
            'anthropic': AnthropicProvider,
            'zhipu': ZhipuProvider,
            'custom': CustomOpenAIProvider
        }

        provider_class = provider_classes.get(provider.lower())
        if not provider_class:
            raise LLMEvaluatorError(f"Unknown provider: {provider}. Use one of: {list(provider_classes.keys())}")

        # Set default models
        default_models = {
            'openai': 'gpt-4o-mini',
            'anthropic': 'claude-3-5-haiku-20241022',
            'zhipu': 'glm-4-flash',
            'custom': 'gpt-4o-mini'  # Will be overridden by CUSTOM_LLM_MODEL env var if set
        }

        if model is None:
            model = default_models.get(provider.lower())

        # Initialize provider (custom provider needs additional parameters)
        if provider.lower() == 'custom':
            base_url = os.environ.get('CUSTOM_LLM_BASE_URL', '')
            if not base_url:
                raise LLMEvaluatorError(
                    "CUSTOM_LLM_BASE_URL environment variable not set for custom provider"
                )
            if model is None:
                model = os.environ.get('CUSTOM_LLM_MODEL', 'gpt-4o-mini')
            self._provider = CustomOpenAIProvider(
                api_key=api_key,
                base_url=base_url,
                model=model,
                delay=delay
            )
        else:
            self._provider = provider_class(api_key=api_key, model=model, delay=delay)

        self.provider_name = provider.lower()
        logger.info(f"LLM Evaluator initialized with provider={provider}, model={model}")

    def evaluate(self, title: str, abstract: str) -> EvalResult:
        """
        Evaluate paper relevance

        Args:
            title: Paper title
            abstract: Paper abstract

        Returns:
            EvalResult with score (0-100)
        """
        if not self.enabled:
            # Return max score if disabled (allow all papers)
            return EvalResult(
                score=100,
                model="disabled",
                provider="disabled",
                raw_response=None
            )

        if not abstract:
            logger.warning(f"No abstract provided for '{title}', returning score 0")
            return EvalResult(
                score=0,
                model=self._provider.model if self._provider else "unknown",
                provider=self.provider_name,
                error="No abstract provided"
            )

        try:
            result = self._provider.evaluate(title, abstract, self.system_prompt)
            logger.debug(f"Evaluated '{title[:50]}...': score={result.score}")
            return result

        except RateLimitError as e:
            logger.error(f"Rate limit exceeded: {e}")
            return EvalResult(
                score=0,
                model=self._provider.model if self._provider else "unknown",
                provider=self.provider_name,
                error=str(e)
            )

        except LLMEvaluatorError as e:
            logger.error(f"LLM evaluation error: {e}")
            return EvalResult(
                score=0,
                model=self._provider.model if self._provider else "unknown",
                provider=self.provider_name,
                error=str(e)
            )

        except Exception as e:
            logger.error(f"Unexpected error during evaluation: {e}")
            return EvalResult(
                score=0,
                model=self._provider.model if self._provider else "unknown",
                provider=self.provider_name,
                error=str(e)
            )
