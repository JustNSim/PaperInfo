"""Update result notifications for external messaging services."""
import base64
import hashlib
import hmac
import logging
import time
from typing import Iterable, Optional

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


def _recent_new_papers(update_log, limit: int = 8):
    """按批次时间窗口查询本次更新新增的论文（用于在通知中附带标题）。

    任何异常都返回空列表，绝不影响通知发送。
    """
    if not getattr(update_log, 'trigger_time', None) or not getattr(update_log, 'total_new', 0):
        return []
    try:
        from datetime import timedelta
        from models import Paper
        window = timedelta(minutes=5)
        return (
            Paper.query
            .filter(Paper.fetched_date >= update_log.trigger_time - window,
                    Paper.fetched_date <= update_log.trigger_time + window)
            .order_by(Paper.llm_score.desc().nulls_last(), Paper.fetched_date.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.debug('查询批次新增论文失败，通知将不附带标题: %s', exc)
        return []


def _format_source_stats(source_stats: dict) -> str:
    if not source_stats:
        return '无'
    labels = {
        'arxiv': 'arXiv',
        'dblp': 'DBLP',
        'semantic_scholar': 'Semantic Scholar',
    }
    return '、'.join(
        f'{labels.get(source, source)} {count} 篇'
        for source, count in source_stats.items()
    )


def build_update_message(update_log, domain_names: Optional[Iterable[str]] = None) -> str:
    """Build a compact mobile-friendly update summary."""
    succeeded = update_log.status == 'success'
    status_label = '成功' if succeeded else ('部分成功' if update_log.status == 'partial' else '失败')
    icon = '✅' if succeeded else ('⚠️' if update_log.status == 'partial' else '❌')
    trigger_label = _TRIGGER_LABELS.get(update_log.trigger_type, update_log.trigger_type)
    trigger_time = (
        update_log.trigger_time.strftime('%Y-%m-%d %H:%M:%S')
        if update_log.trigger_time else '未知'
    )
    names = list(domain_names or [])

    lines = [
        f'{icon} PaperInfo {trigger_label}{status_label}',
        f'时间：{trigger_time}',
        f'领域：{"、".join(names) if names else "无"}',
        f'新增：{update_log.total_new or 0} 篇',
        f'来源：{_format_source_stats(update_log.source_stats or {})}',
    ]

    if update_log.llm_filtered:
        lines.append(f'LLM 过滤：{update_log.llm_filtered} 篇')

    details = update_log.operation_details or {}
    if details.get('duration_seconds') is not None:
        lines.append(f'耗时：{details["duration_seconds"]} 秒')

    source_failures = details.get('source_failures') or {}
    if source_failures:
        # 附上熔断原因，便于区分"对方临时故障"与配置问题
        lines.append(
            f'来源异常：{"、".join(f"{k}（{v}）" for k, v in source_failures.items())}'
        )

    if update_log.error_message:
        error = str(update_log.error_message).replace('\n', ' ').strip()
        lines.append(f'错误：{error[:500]}')

    # 附上前几篇新增论文标题（按相关度排序），手机不看电脑也能了解更新内容
    papers = _recent_new_papers(update_log)
    if papers:
        lines.append('新增论文：')
        for i, paper in enumerate(papers, 1):
            title = paper.title
            if paper.llm_score is not None:
                title += f'（{paper.llm_score}/{paper.llm_value_score}）'
            lines.append(f'{i}. {title}')
        if (update_log.total_new or 0) > len(papers):
            lines.append(f'… 共 {update_log.total_new} 篇')

    base_url = (Config.PAPERINFO_PUBLIC_URL or '').rstrip('/')
    if base_url and getattr(update_log, 'id', None):
        lines.append(f'详情：{base_url}/?update_log={update_log.id}')

    return '\n'.join(lines)


def send_update_notification(update_log, domain_names: Optional[Iterable[str]] = None) -> bool:
    """Send an update summary to Feishu; notification errors never fail a fetch."""
    webhook_url = Config.FEISHU_WEBHOOK_URL
    if not webhook_url or update_log.trigger_type not in _TRIGGER_LABELS:
        return False

    payload = {
        'msg_type': 'text',
        'content': {'text': build_update_message(update_log, domain_names)},
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
