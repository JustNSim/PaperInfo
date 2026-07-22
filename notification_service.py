"""Update result notifications for external messaging services."""
import base64
import hashlib
import hmac
import logging
import re
import time
from typing import Iterable, Optional
from urllib.parse import quote

import requests

from config import Config


logger = logging.getLogger(__name__)

_TRIGGER_LABELS = {
    'scheduled': '定时更新',
    'manual': '手动更新',
    'catch_up': '补执行更新',
}


def _feishu_signature(timestamp: int, secret: str) -> str:
    """Create the signature required by a signed Feishu custom bot."""
    string_to_sign = f'{timestamp}\n{secret}'
    digest = hmac.new(
        string_to_sign.encode('utf-8'),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode('utf-8')


def _recent_new_papers(update_log, limit: int = 5):
    """按批次时间窗口查询本次更新新增的论文（用于在通知中附带标题）。

    任何异常都返回空列表，绝不影响通知发送。
    """
    if not getattr(update_log, 'trigger_time', None) or not getattr(update_log, 'total_new', 0):
        return []
    try:
        from datetime import datetime
        from models import Paper

        details = getattr(update_log, 'operation_details', None) or {}
        started_at_value = details.get('started_at')
        domain_ids = list(getattr(update_log, 'domains_processed', None) or [])
        if not started_at_value or not domain_ids:
            return []

        started_at = (
            datetime.fromisoformat(started_at_value)
            if isinstance(started_at_value, str) else started_at_value
        )
        return (
            Paper.query
            .filter(
                Paper.fetched_date >= started_at,
                Paper.fetched_date <= update_log.trigger_time,
                Paper.domain_id.in_(domain_ids),
            )
            .order_by(Paper.llm_score.desc().nulls_last(), Paper.fetched_date.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.debug('查询批次新增论文失败，通知将不附带标题: %s', exc)
        return []


def _source_label(source: str) -> str:
    labels = {
        'arxiv': 'arXiv',
        'dblp': 'DBLP',
        's2': 'Semantic Scholar',
        'semantic_scholar': 'Semantic Scholar',
    }
    return labels.get(source, source)


def _markdown_text(value: str) -> str:
    """Escape the Markdown characters used by paper titles and abstracts."""
    text = str(value or '').replace('\\', '\\\\')
    for char in ('*', '_', '~', '[', ']', '`'):
        text = text.replace(char, f'\\{char}')
    return text


def _markdown_url(url: str) -> str:
    return quote(str(url or ''), safe=":/?#@!$&'*+,;=%")


def _abstract_summary(abstract: str, limit: int = 100) -> str:
    """Return a single compact abstract snippet no longer than ``limit`` chars."""
    text = re.sub(r'\s+', ' ', str(abstract or '')).strip()
    if not text:
        return ''
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + '…'


def _summary_line(update_log) -> str:
    parts = [f'新增 {update_log.total_new or 0} 篇']
    for source, count in (update_log.source_stats or {}).items():
        if count:
            parts.append(f'{_source_label(source)} {count}')
    duration = (update_log.operation_details or {}).get('duration_seconds')
    if duration is not None:
        parts.append(f'耗时 {duration:g} 秒' if isinstance(duration, (int, float)) else f'耗时 {duration} 秒')
    return '｜'.join(parts)


def _paper_markdown(paper, index: int) -> str:
    title = _markdown_text(paper.title)
    if paper.url:
        title = f'[{title}]({_markdown_url(paper.url)})'

    published = paper.published_date.strftime('%m-%d') if paper.published_date else '日期未知'
    metadata = '｜'.join([
        _source_label(paper.source),
        f'相关 {paper.llm_score if paper.llm_score is not None else "-"}',
        f'价值 {paper.llm_value_score if paper.llm_value_score is not None else "-"}',
        published,
    ])
    lines = [f'**{index}. {title}**', metadata]
    summary = _abstract_summary(paper.abstract)
    if summary:
        lines.append(_markdown_text(summary))
    return '\n'.join(lines)


def build_update_card(update_log, domain_names: Optional[Iterable[str]] = None) -> dict:
    """Build a mobile-friendly Feishu card containing at most five papers."""
    del domain_names  # Reserved for future card variants without changing the public API.
    status_labels = {
        'success': ('更新成功', 'green'),
        'partial': ('部分成功', 'orange'),
        'failed': ('更新失败', 'red'),
    }
    status_label, template = status_labels.get(
        update_log.status, (str(update_log.status), 'blue')
    )
    elements = [{
        'tag': 'div',
        'text': {'tag': 'lark_md', 'content': _summary_line(update_log)},
    }]

    details = update_log.operation_details or {}
    source_failures = details.get('source_failures') or {}
    if source_failures:
        failures = '；'.join(f'{source}：{reason}' for source, reason in source_failures.items())
        elements.append({
            'tag': 'div',
            'text': {'tag': 'lark_md', 'content': f'**来源异常：**{_markdown_text(failures)}'},
        })
    if update_log.error_message:
        error = re.sub(r'\s+', ' ', str(update_log.error_message)).strip()[:500]
        elements.append({
            'tag': 'div',
            'text': {'tag': 'lark_md', 'content': f'**错误：**{_markdown_text(error)}'},
        })

    papers = _recent_new_papers(update_log, limit=5)[:5]
    for index, paper in enumerate(papers, 1):
        elements.extend([
            {'tag': 'hr'},
            {
                'tag': 'div',
                'text': {'tag': 'lark_md', 'content': _paper_markdown(paper, index)},
            },
        ])

    remaining = max((update_log.total_new or 0) - len(papers), 0)
    if remaining:
        elements.append({
            'tag': 'div',
            'text': {'tag': 'lark_md', 'content': f'*另有 {remaining} 篇*'},
        })

    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'template': template,
            'title': {'tag': 'plain_text', 'content': f'PaperInfo {status_label}'},
        },
        'elements': elements,
    }


def build_update_message(update_log, domain_names: Optional[Iterable[str]] = None) -> str:
    """Build a plain-text representation, retained for logs and compatibility."""
    card = build_update_card(update_log, domain_names)
    lines = [card['header']['title']['content']]
    lines.extend(
        element['text']['content']
        for element in card['elements']
        if element.get('tag') == 'div'
    )
    return '\n\n'.join(lines)


def send_update_notification(update_log, domain_names: Optional[Iterable[str]] = None) -> bool:
    """Send an update summary to Feishu; notification errors never fail a fetch."""
    webhook_url = Config.FEISHU_WEBHOOK_URL
    if not webhook_url or update_log.trigger_type not in _TRIGGER_LABELS:
        return False

    payload = {
        'msg_type': 'interactive',
        'card': build_update_card(update_log, domain_names),
    }

    if Config.FEISHU_WEBHOOK_SECRET:
        timestamp = int(time.time())
        payload.update({
            'timestamp': str(timestamp),
            'sign': _feishu_signature(timestamp, Config.FEISHU_WEBHOOK_SECRET),
        })

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=Config.FEISHU_NOTIFICATION_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
        result_code = result.get('code', result.get('StatusCode', 0))
        if result_code != 0:
            raise RuntimeError(result.get('msg') or result.get('StatusMessage') or str(result))
        logger.info('飞书更新通知发送成功: update_log=%s', update_log.id)
        return True
    except Exception as exc:
        logger.warning('飞书更新通知发送失败，不影响本次更新: %s', exc)
        return False
