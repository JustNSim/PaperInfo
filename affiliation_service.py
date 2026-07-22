"""Rate-limited author affiliation enrichment using OpenAlex."""
import logging
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote

import requests

from config import Config
from models import Paper, db, get_beijing_time


logger = logging.getLogger(__name__)
OPENALEX_API_URL = 'https://api.openalex.org'

_app = None
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='affiliation-enrichment')
_status_lock = threading.Lock()
_backfill_status = {
    'running': False,
    'total': 0,
    'processed': 0,
    'enriched': 0,
    'paper_not_found': 0,
    'affiliation_missing': 0,
    'failed': 0,
    'started_at': None,
    'completed_at': None,
}


def init_affiliation_service(app):
    global _app
    _app = app


def _normalize_title(value: str) -> str:
    value = unicodedata.normalize('NFKC', value or '').casefold()
    return ''.join(char for char in value if char.isalnum())


def _normalize_arxiv_id(value: str) -> str:
    value = str(value or '').strip().split('/')[-1]
    return re.sub(r'v\d+$', '', value, flags=re.IGNORECASE)


def _parse_openalex_work(work: dict) -> list:
    """Convert OpenAlex work-level authorships into the local JSON format."""
    details = []
    for authorship in work.get('authorships') or []:
        author = authorship.get('author') or {}
        name = str(author.get('display_name') or '').strip()
        affiliations = []
        seen = set()
        for institution in authorship.get('institutions') or []:
            institution_name = str(institution.get('display_name') or '').strip()
            key = institution_name.casefold()
            if institution_name and key not in seen:
                seen.add(key)
                affiliations.append(institution_name)
        if not affiliations:
            for raw_name in authorship.get('raw_affiliation_strings') or []:
                raw_name = str(raw_name or '').strip()
                key = raw_name.casefold()
                if raw_name and key not in seen:
                    seen.add(key)
                    affiliations.append(raw_name)
        if name and affiliations:
            details.append({'name': name, 'affiliations': affiliations})
    return details


class OpenAlexAffiliationClient:
    def __init__(self, api_key=None, session=None):
        self.api_key = api_key or Config.OPENALEX_API_KEY
        self.session = session or requests.Session()
        self._last_request_at = 0.0

    def close(self):
        self.session.close()

    def _request(self, path: str, params=None):
        if not self.api_key:
            return None
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < Config.OPENALEX_DELAY:
            time.sleep(Config.OPENALEX_DELAY - elapsed)

        query = dict(params or {})
        query['api_key'] = self.api_key
        for attempt in range(Config.OPENALEX_MAX_RETRIES + 1):
            try:
                self._last_request_at = time.monotonic()
                response = self.session.get(
                    f'{OPENALEX_API_URL}{path}',
                    params=query,
                    timeout=Config.OPENALEX_REQUEST_TIMEOUT,
                )
                if response.status_code == 404:
                    return None
                if response.status_code in (429, 500, 502, 503, 504) and attempt < Config.OPENALEX_MAX_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException:
                if attempt >= Config.OPENALEX_MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)
        return None

    def _lookup_by_doi(self, doi: str):
        if not doi:
            return None
        encoded = quote(doi.strip().removeprefix('https://doi.org/'), safe='/')
        return self._request(
            f'/works/doi:{encoded}',
            {'select': 'id,title,publication_year,authorships'},
        )

    def _lookup_by_title(self, title: str, year=None):
        params = {
            'search': title,
            'per_page': 5,
            'select': 'id,title,publication_year,authorships',
        }
        if year:
            params['filter'] = f'publication_year:{year}'
        response = self._request('/works', params) or {}
        expected = _normalize_title(title)
        return next(
            (work for work in response.get('results', [])
             if _normalize_title(work.get('title')) == expected),
            None,
        )

    def fetch_result_for_paper(self, paper: Paper):
        """Return a precise lookup outcome and any author-affiliation details."""
        doi = paper.doi
        if not doi and paper.source == 'arxiv' and paper.source_id:
            doi = f'10.48550/arXiv.{_normalize_arxiv_id(paper.source_id)}'
        work = self._lookup_by_doi(doi) if doi else None
        if not work:
            work = self._lookup_by_title(paper.title, paper.year)
        if not work:
            return 'paper_not_found', []
        details = _parse_openalex_work(work)
        if not details:
            return 'affiliation_missing', []
        return 'success', details

    def fetch_for_paper(self, paper: Paper):
        """Compatibility helper returning only author-affiliation details."""
        _, details = self.fetch_result_for_paper(paper)
        return details


def _update_papers(paper_ids, track_backfill=False):
    if _app is None or not Config.OPENALEX_API_KEY:
        return
    client = OpenAlexAffiliationClient()
    try:
        with _app.app_context():
            for paper_id in paper_ids:
                paper = db.session.get(Paper, paper_id)
                if not paper or paper.author_affiliations:
                    if track_backfill:
                        _advance_status('processed')
                    continue
                try:
                    outcome, details = client.fetch_result_for_paper(paper)
                    paper.affiliations_fetched_at = get_beijing_time()
                    paper.affiliations_source = 'openalex'
                    if outcome == 'success':
                        paper.author_affiliations = details
                        paper.affiliations_status = 'success'
                        outcome = 'enriched'
                    else:
                        paper.affiliations_status = outcome
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    paper = db.session.get(Paper, paper_id)
                    if paper:
                        paper.affiliations_status = 'error'
                        paper.affiliations_fetched_at = get_beijing_time()
                        db.session.commit()
                    outcome = 'failed'
                    logger.warning('OpenAlex 单位补全失败 paper_id=%s: %s', paper_id, exc)
                if track_backfill:
                    _advance_status('processed', outcome)
    finally:
        client.close()


def enqueue_affiliation_enrichment(paper_ids):
    """Queue newly inserted papers without delaying the fetch job."""
    ids = list(dict.fromkeys(paper_ids or []))
    if not ids or not Config.OPENALEX_API_KEY or _app is None:
        return 0
    _executor.submit(_update_papers, ids, False)
    return len(ids)


def _advance_status(counter, outcome=None):
    with _status_lock:
        _backfill_status[counter] += 1
        if outcome:
            _backfill_status[outcome] += 1


def _run_backfill():
    try:
        with _app.app_context():
            paper_ids = [row[0] for row in db.session.query(Paper.id).filter(
                db.or_(
                    Paper.affiliations_status.is_(None),
                    # Include the legacy combined status once so the next
                    # backfill can split it into the two precise outcomes.
                    Paper.affiliations_status.in_(['pending', 'error', 'not_found']),
                )
            ).order_by(Paper.id.asc()).all()]
        with _status_lock:
            _backfill_status['total'] = len(paper_ids)
        _update_papers(paper_ids, True)
    finally:
        with _status_lock:
            _backfill_status['running'] = False
            _backfill_status['completed_at'] = get_beijing_time().isoformat()


def start_affiliation_backfill():
    if not Config.OPENALEX_API_KEY:
        return False, '尚未配置 PAPERINFO_OPENALEX_API_KEY'
    if _app is None:
        return False, '单位补全服务尚未初始化'
    with _status_lock:
        if _backfill_status['running']:
            return False, '作者单位回填任务正在运行'
        _backfill_status.update({
            'running': True,
            'total': 0,
            'processed': 0,
            'enriched': 0,
            'paper_not_found': 0,
            'affiliation_missing': 0,
            'failed': 0,
            'started_at': get_beijing_time().isoformat(),
            'completed_at': None,
        })
    _executor.submit(_run_backfill)
    return True, '作者单位回填任务已在后台启动'


def get_affiliation_backfill_status():
    with _status_lock:
        return dict(_backfill_status)
