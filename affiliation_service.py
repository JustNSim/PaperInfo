"""Rate-limited, layered paper metadata enrichment with persistent retries."""
import logging
import html
import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import quote, urljoin

import feedparser
import requests

from config import Config
from models import Paper, db, get_beijing_time


logger = logging.getLogger(__name__)
OPENALEX_API_URL = 'https://api.openalex.org'
UNPAYWALL_API_URL = 'https://api.unpaywall.org/v2'
CROSSREF_API_URL = 'https://api.crossref.org/works'
ARXIV_API_URL = 'https://export.arxiv.org/api/query'

_app = None
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='affiliation-enrichment')
_status_lock = threading.Lock()
_backfill_status = {
    'running': False,
    'total': 0,
    'processed': 0,
    'enriched': 0,
    'abstract_enriched': 0,
    'pdf_url_enriched': 0,
    'openalex_abstract_enriched': 0,
    'openalex_pdf_url_enriched': 0,
    'unpaywall_pdf_url_enriched': 0,
    'crossref_abstract_enriched': 0,
    'crossref_pdf_url_enriched': 0,
    'arxiv_abstract_enriched': 0,
    'arxiv_pdf_url_enriched': 0,
    'publisher_abstract_enriched': 0,
    'publisher_pdf_url_enriched': 0,
    'paper_not_found': 0,
    'affiliation_missing': 0,
    'failed': 0,
    'started_at': None,
    'completed_at': None,
}

OPENALEX_WORK_FIELDS = (
    'id,title,publication_year,authorships,abstract_inverted_index,'
    'best_oa_location,primary_location,locations'
)


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


def _parse_openalex_abstract(work: dict) -> str:
    """Rebuild OpenAlex's inverted abstract index as readable plain text."""
    inverted_index = work.get('abstract_inverted_index') or {}
    positioned_words = []
    for word, positions in inverted_index.items():
        for position in positions or []:
            if isinstance(position, int):
                positioned_words.append((position, str(word)))
    positioned_words.sort(key=lambda item: item[0])
    return ' '.join(word for _position, word in positioned_words).strip()


def _parse_openalex_pdf_url(work: dict) -> str | None:
    """Return the first explicit OpenAlex PDF URL, preferring its best OA copy."""
    locations = [work.get('best_oa_location'), work.get('primary_location')]
    locations.extend(work.get('locations') or [])
    for location in locations:
        pdf_url = str((location or {}).get('pdf_url') or '').strip()
        if pdf_url.lower().startswith(('http://', 'https://')):
            return pdf_url
    return None


def _parse_openalex_metadata(work: dict) -> dict:
    return {
        'author_affiliations': _parse_openalex_work(work),
        'abstract': _parse_openalex_abstract(work) or None,
        'pdf_url': _parse_openalex_pdf_url(work),
    }


def _parse_unpaywall_pdf_url(record: dict) -> str | None:
    """Return Unpaywall's best explicit OA PDF URL."""
    locations = [record.get('best_oa_location')]
    locations.extend(record.get('oa_locations') or [])
    seen = set()
    for location in locations:
        pdf_url = str((location or {}).get('url_for_pdf') or '').strip()
        if pdf_url in seen:
            continue
        seen.add(pdf_url)
        if pdf_url.lower().startswith(('http://', 'https://')):
            return pdf_url
    return None


class UnpaywallClient:
    def __init__(self, email=None, session=None):
        self.email = email or Config.UNPAYWALL_EMAIL
        self.session = session or requests.Session()
        self._owns_session = session is None
        self._last_request_at = 0.0

    def close(self):
        if self._owns_session:
            self.session.close()

    def fetch_pdf_url(self, doi: str) -> str | None:
        if not self.email or not doi:
            return None
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < Config.UNPAYWALL_DELAY:
            time.sleep(Config.UNPAYWALL_DELAY - elapsed)

        normalized_doi = doi.strip().removeprefix('https://doi.org/')
        encoded_doi = quote(normalized_doi, safe='')
        for attempt in range(Config.UNPAYWALL_MAX_RETRIES + 1):
            try:
                self._last_request_at = time.monotonic()
                response = self.session.get(
                    f'{UNPAYWALL_API_URL}/{encoded_doi}',
                    params={'email': self.email},
                    headers={'User-Agent': f'PaperInfo/1.0 (mailto:{self.email})'},
                    timeout=Config.UNPAYWALL_REQUEST_TIMEOUT,
                )
                if response.status_code == 404:
                    return None
                if (
                    response.status_code in (429, 500, 502, 503, 504)
                    and attempt < Config.UNPAYWALL_MAX_RETRIES
                ):
                    time.sleep(2 ** attempt)
                    continue
                response.raise_for_status()
                return _parse_unpaywall_pdf_url(response.json())
            except requests.RequestException:
                if attempt >= Config.UNPAYWALL_MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)
        return None


def _plain_text(value: str) -> str:
    value = re.sub(r'<[^>]+>', ' ', str(value or ''))
    value = re.sub(r'\s+', ' ', html.unescape(value)).strip()
    return re.sub(r'\s+([,.;:!?])', r'\1', value)


class CrossrefMetadataClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._owns_session = session is None
        self._last_request_at = 0.0
        self.mailto = Config.CROSSREF_MAILTO or Config.UNPAYWALL_EMAIL

    def close(self):
        if self._owns_session:
            self.session.close()

    def fetch_metadata(self, doi: str) -> dict:
        result = {'abstract': None, 'pdf_urls': []}
        if not doi:
            return result
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < Config.CROSSREF_DELAY:
            time.sleep(Config.CROSSREF_DELAY - elapsed)
        normalized_doi = doi.strip().removeprefix('https://doi.org/')
        params = {'mailto': self.mailto} if self.mailto else None
        response = None
        for attempt in range(Config.CROSSREF_MAX_RETRIES + 1):
            self._last_request_at = time.monotonic()
            response = self.session.get(
                f'{CROSSREF_API_URL}/{quote(normalized_doi, safe="")}',
                params=params,
                headers={'User-Agent': 'PaperInfo/1.0 (metadata enrichment)'},
                timeout=Config.CROSSREF_REQUEST_TIMEOUT,
            )
            if response.status_code == 404:
                return result
            if (
                response.status_code in (429, 500, 502, 503, 504)
                and attempt < Config.CROSSREF_MAX_RETRIES
            ):
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            break
        message = (response.json() or {}).get('message') or {}
        result['abstract'] = _plain_text(message.get('abstract')) or None
        result['pdf_urls'] = list(dict.fromkeys(
            str(link.get('URL') or '').strip()
            for link in message.get('link') or []
            if 'pdf' in str(link.get('content-type') or '').casefold()
            and str(link.get('URL') or '').lower().startswith(('http://', 'https://'))
        ))
        return result


def _author_last_names(values) -> set:
    names = set()
    for value in values or []:
        normalized = re.sub(r'[^\w\s-]', ' ', str(value or '').casefold())
        tokens = [token for token in normalized.split() if not token.isdigit()]
        if tokens:
            names.add(tokens[-1])
    return names


class ArxivMetadataClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._owns_session = session is None
        self._last_request_at = 0.0

    def close(self):
        if self._owns_session:
            self.session.close()

    def fetch_metadata(self, paper: Paper) -> dict:
        result = {'abstract': None, 'pdf_urls': []}
        if not paper.title:
            return result
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < Config.ARXIV_DELAY:
            time.sleep(Config.ARXIV_DELAY - elapsed)
        self._last_request_at = time.monotonic()
        response = self.session.get(
            ARXIV_API_URL,
            params={
                'search_query': f'ti:"{str(paper.title).replace(chr(34), " ")}"',
                'start': 0,
                'max_results': 5,
            },
            headers={'User-Agent': 'PaperInfo/1.0 (metadata enrichment)'},
            timeout=Config.ARXIV_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        expected_title = _normalize_title(paper.title)
        expected_authors = _author_last_names(paper.authors)
        for entry in feed.entries:
            if _normalize_title(entry.get('title')) != expected_title:
                continue
            published = str(entry.get('published') or '')
            try:
                published_year = int(published[:4])
            except (TypeError, ValueError):
                published_year = None
            if paper.year and published_year and abs(paper.year - published_year) > 1:
                continue
            entry_authors = _author_last_names(
                author.get('name') for author in entry.get('authors') or []
            )
            if expected_authors and entry_authors and not (expected_authors & entry_authors):
                continue
            result['abstract'] = _plain_text(entry.get('summary')) or None
            result['pdf_urls'] = [
                str(link.get('href'))
                for link in entry.get('links') or []
                if (
                    str(link.get('type') or '').casefold() == 'application/pdf'
                    or str(link.get('title') or '').casefold() == 'pdf'
                )
                and str(link.get('href') or '').lower().startswith(('http://', 'https://'))
            ]
            if not result['pdf_urls'] and entry.get('id'):
                arxiv_id = str(entry['id']).rstrip('/').split('/')[-1]
                result['pdf_urls'] = [f'https://arxiv.org/pdf/{arxiv_id}']
            return result
        return result


class _MetaTagParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.json_ld = []
        self._json_ld_parts = None

    def handle_starttag(self, tag, attrs):
        attributes = {str(key).casefold(): value for key, value in attrs}
        if (
            tag.casefold() == 'script'
            and str(attributes.get('type') or '').casefold() == 'application/ld+json'
        ):
            self._json_ld_parts = []
            return
        if tag.casefold() != 'meta':
            return
        key = (
            attributes.get('name')
            or attributes.get('property')
            or attributes.get('itemprop')
        )
        content = attributes.get('content')
        if key and content:
            self.values.setdefault(str(key).casefold(), str(content).strip())

    def handle_data(self, data):
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag):
        if tag.casefold() == 'script' and self._json_ld_parts is not None:
            self.json_ld.append(''.join(self._json_ld_parts))
            self._json_ld_parts = None


class PublisherMetadataClient:
    ABSTRACT_KEYS = (
        'citation_abstract', 'dc.description', 'prism.abstract', 'og:description'
    )
    PDF_KEYS = ('citation_pdf_url', 'pdf_url')

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._owns_session = session is None
        self._last_request_at = 0.0

    def close(self):
        if self._owns_session:
            self.session.close()

    def fetch_metadata(self, paper: Paper) -> dict:
        result = {'abstract': None, 'pdf_urls': []}
        if not str(paper.url or '').lower().startswith(('http://', 'https://')):
            return result
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < Config.METADATA_PAGE_DELAY:
            time.sleep(Config.METADATA_PAGE_DELAY - elapsed)
        self._last_request_at = time.monotonic()
        response = self.session.get(
            paper.url,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 Chrome/124 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml',
            },
            timeout=Config.CROSSREF_REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        if response.status_code == 202 and not response.content:
            return result
        response.raise_for_status()
        if 'html' not in str(response.headers.get('Content-Type') or '').casefold():
            return result
        parser = _MetaTagParser()
        parser.feed(response.text)
        page_title = (
            parser.values.get('citation_title')
            or parser.values.get('dc.title')
            or parser.values.get('og:title')
        )
        json_ld_nodes = []
        for raw_json in parser.json_ld:
            try:
                value = json.loads(raw_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                if not isinstance(item, dict):
                    continue
                graph = item.get('@graph')
                json_ld_nodes.extend(graph if isinstance(graph, list) else [item])
        matching_json_ld = next((
            node for node in json_ld_nodes
            if isinstance(node, dict)
            and _normalize_title(node.get('headline') or node.get('name'))
            == _normalize_title(paper.title)
        ), None)
        if not page_title and matching_json_ld:
            page_title = matching_json_ld.get('headline') or matching_json_ld.get('name')
        if not page_title or _normalize_title(page_title) != _normalize_title(paper.title):
            return result
        for key in self.ABSTRACT_KEYS:
            abstract = _plain_text(parser.values.get(key))
            if len(abstract) >= 40:
                result['abstract'] = abstract
                break
        if not result['abstract'] and matching_json_ld:
            abstract = _plain_text(matching_json_ld.get('description'))
            if len(abstract) >= 40:
                result['abstract'] = abstract
        result['pdf_urls'] = list(dict.fromkeys(
            urljoin(response.url, parser.values[key])
            for key in self.PDF_KEYS
            if parser.values.get(key)
        ))
        if matching_json_ld:
            for key in ('contentUrl', 'url'):
                candidate = str(matching_json_ld.get(key) or '').strip()
                if candidate.lower().endswith('.pdf'):
                    result['pdf_urls'].append(urljoin(response.url, candidate))
        result['pdf_urls'] = list(dict.fromkeys(result['pdf_urls']))
        return result


class PDFURLValidator:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._owns_session = session is None

    def close(self):
        if self._owns_session:
            self.session.close()

    def first_valid(self, candidates) -> str | None:
        for candidate in dict.fromkeys(candidates or []):
            if self.is_public_pdf(candidate):
                return candidate
        return None

    def is_public_pdf(self, url: str) -> bool:
        if not str(url or '').lower().startswith(('http://', 'https://')):
            return False
        response = None
        try:
            response = self.session.get(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (PaperInfo PDF availability check)',
                    'Accept': 'application/pdf,*/*;q=0.1',
                    'Range': 'bytes=0-1023',
                },
                timeout=Config.METADATA_PDF_PROBE_TIMEOUT,
                allow_redirects=True,
                stream=True,
            )
            if response.status_code not in (200, 206):
                return False
            prefix = b''
            for chunk in response.iter_content(chunk_size=1024):
                prefix += chunk
                if len(prefix) >= 1024:
                    break
            return b'%PDF-' in prefix[:1024]
        except requests.RequestException:
            return False
        finally:
            if response is not None:
                response.close()


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
            {'select': OPENALEX_WORK_FIELDS},
        )

    def _lookup_by_title(self, title: str, year=None):
        params = {
            'search': title,
            'per_page': 5,
            'select': OPENALEX_WORK_FIELDS,
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

    def fetch_metadata_for_paper(self, paper: Paper):
        """Return the lookup outcome and all reusable work metadata."""
        doi = paper.doi
        if not doi and paper.source == 'arxiv' and paper.source_id:
            doi = f'10.48550/arXiv.{_normalize_arxiv_id(paper.source_id)}'
        work = self._lookup_by_doi(doi) if doi else None
        if not work:
            work = self._lookup_by_title(paper.title, paper.year)
        if not work:
            return 'paper_not_found', {
                'author_affiliations': [], 'abstract': None, 'pdf_url': None,
            }
        metadata = _parse_openalex_metadata(work)
        outcome = (
            'success' if metadata['author_affiliations']
            else 'affiliation_missing'
        )
        return outcome, metadata

    def fetch_result_for_paper(self, paper: Paper):
        """Compatibility helper returning affiliation outcome and details."""
        outcome, metadata = self.fetch_metadata_for_paper(paper)
        return outcome, metadata['author_affiliations']

    def fetch_for_paper(self, paper: Paper):
        """Compatibility helper returning only author-affiliation details."""
        _, details = self.fetch_result_for_paper(paper)
        return details


def _schedule_next_metadata_retry(paper: Paper, now: datetime):
    """Schedule retries near day 1, 3, 7 and 30 after each first attempt."""
    attempt = int(paper.metadata_retry_count or 0) + 1
    paper.metadata_retry_count = attempt
    if str(paper.abstract or '').strip() and str(paper.pdf_url or '').strip():
        paper.metadata_next_retry_at = None
        return
    offsets = tuple(sorted(set(Config.METADATA_RETRY_HOURS)))
    retry_index = attempt - 1
    if retry_index >= len(offsets):
        paper.metadata_next_retry_at = None
        return
    previous_offset = offsets[retry_index - 1] if retry_index else 0
    delay_hours = max(1, offsets[retry_index] - previous_offset)
    paper.metadata_next_retry_at = now + timedelta(hours=delay_hours)


def _update_papers(paper_ids, track_backfill=False):
    if _app is None:
        return
    client = OpenAlexAffiliationClient()
    unpaywall_client = UnpaywallClient()
    crossref_client = CrossrefMetadataClient()
    arxiv_client = ArxivMetadataClient()
    publisher_client = PublisherMetadataClient()
    pdf_validator = PDFURLValidator()
    try:
        with _app.app_context():
            for paper_id in paper_ids:
                paper = db.session.get(Paper, paper_id)
                if not paper:
                    if track_backfill:
                        _advance_status('processed')
                    continue
                needs_affiliations = not paper.author_affiliations
                needs_abstract = not str(paper.abstract or '').strip()
                needs_pdf_url = not str(paper.pdf_url or '').strip()
                if not (needs_affiliations or needs_abstract or needs_pdf_url):
                    if track_backfill:
                        _advance_status('processed')
                    continue
                counters = []
                try:
                    try:
                        outcome, metadata = client.fetch_metadata_for_paper(paper)
                    except Exception as exc:
                        outcome = 'error'
                        metadata = {
                            'author_affiliations': [],
                            'abstract': None,
                            'pdf_url': None,
                        }
                        logger.warning(
                            'OpenAlex 元数据补全失败 paper_id=%s: %s',
                            paper_id, exc,
                        )
                    if needs_affiliations:
                        paper.affiliations_fetched_at = get_beijing_time()
                        paper.affiliations_source = 'openalex'
                        if outcome == 'success':
                            paper.author_affiliations = metadata['author_affiliations']
                            paper.affiliations_status = 'success'
                            counters.append('enriched')
                        else:
                            paper.affiliations_status = outcome
                            counters.append('failed' if outcome == 'error' else outcome)
                    abstract = metadata['abstract'] if needs_abstract else None
                    abstract_source = 'openalex' if abstract else None
                    pdf_candidates = []
                    pdf_url = None
                    pdf_source = None
                    if needs_pdf_url and metadata['pdf_url']:
                        pdf_candidates.append(('openalex', metadata['pdf_url']))

                    if paper.doi and (needs_abstract or needs_pdf_url):
                        try:
                            crossref = crossref_client.fetch_metadata(paper.doi)
                            if not abstract and crossref['abstract']:
                                abstract = crossref['abstract']
                                abstract_source = 'crossref'
                            pdf_candidates.extend(
                                ('crossref', url) for url in crossref['pdf_urls']
                            )
                        except Exception as exc:
                            logger.warning(
                                'Crossref 元数据补全失败 paper_id=%s: %s',
                                paper_id, exc,
                            )

                    if needs_pdf_url and paper.doi and Config.UNPAYWALL_EMAIL:
                        try:
                            unpaywall_pdf = unpaywall_client.fetch_pdf_url(paper.doi)
                            if unpaywall_pdf:
                                pdf_candidates.append(('unpaywall', unpaywall_pdf))
                        except Exception as exc:
                            logger.warning(
                                'Unpaywall PDF 补全失败 paper_id=%s: %s',
                                paper_id, exc,
                            )

                    if needs_pdf_url:
                        for candidate_source, candidate_url in pdf_candidates:
                            if pdf_validator.is_public_pdf(candidate_url):
                                pdf_url = candidate_url
                                pdf_source = candidate_source
                                break

                    if (needs_abstract and not abstract) or (needs_pdf_url and not pdf_url):
                        try:
                            arxiv = arxiv_client.fetch_metadata(paper)
                            if not abstract and arxiv['abstract']:
                                abstract = arxiv['abstract']
                                abstract_source = 'arxiv'
                            if needs_pdf_url and not pdf_url:
                                pdf_url = pdf_validator.first_valid(arxiv['pdf_urls'])
                                if pdf_url:
                                    pdf_source = 'arxiv'
                        except Exception as exc:
                            logger.warning(
                                'arXiv 元数据补全失败 paper_id=%s: %s',
                                paper_id, exc,
                            )

                    if (needs_abstract and not abstract) or (needs_pdf_url and not pdf_url):
                        try:
                            publisher = publisher_client.fetch_metadata(paper)
                            if not abstract and publisher['abstract']:
                                abstract = publisher['abstract']
                                abstract_source = 'publisher'
                            if needs_pdf_url and not pdf_url:
                                pdf_url = pdf_validator.first_valid(
                                    publisher['pdf_urls']
                                )
                                if pdf_url:
                                    pdf_source = 'publisher'
                        except Exception as exc:
                            logger.warning(
                                '出版社页面元数据补全失败 paper_id=%s: %s',
                                paper_id, exc,
                            )

                    if needs_abstract and abstract:
                        paper.abstract = abstract
                        paper.abstract_source = abstract_source
                        counters.append('abstract_enriched')
                        source_counter = f'{abstract_source}_abstract_enriched'
                        if source_counter in _backfill_status:
                            counters.append(source_counter)

                    if needs_pdf_url:
                        if pdf_url:
                            paper.pdf_url = pdf_url
                            paper.pdf_source = pdf_source
                            counters.append('pdf_url_enriched')
                            source_counter = f'{pdf_source}_pdf_url_enriched'
                            if source_counter in _backfill_status:
                                counters.append(source_counter)

                    now = get_beijing_time()
                    paper.metadata_fetched_at = now
                    _schedule_next_metadata_retry(paper, now)
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    paper = db.session.get(Paper, paper_id)
                    if paper and needs_affiliations:
                        paper.affiliations_status = 'error'
                        paper.affiliations_fetched_at = get_beijing_time()
                        db.session.commit()
                    counters = ['failed']
                    logger.warning('论文元数据补全失败 paper_id=%s: %s', paper_id, exc)
                if track_backfill:
                    _advance_status('processed')
                    for counter in counters:
                        _advance_status(counter)
    finally:
        client.close()
        unpaywall_client.close()
        crossref_client.close()
        arxiv_client.close()
        publisher_client.close()
        pdf_validator.close()


def enqueue_affiliation_enrichment(paper_ids):
    """Queue newly inserted papers without delaying the fetch job."""
    ids = list(dict.fromkeys(paper_ids or []))
    if not ids or _app is None:
        return 0
    _executor.submit(_update_papers, ids, False)
    return len(ids)


def enqueue_due_metadata_retries():
    """Queue a bounded batch of incomplete papers whose retry time has arrived."""
    if _app is None:
        return 0
    with _app.app_context():
        now = get_beijing_time()
        paper_ids = [row[0] for row in db.session.query(Paper.id).filter(
            Paper.metadata_next_retry_at.isnot(None),
            Paper.metadata_next_retry_at <= now,
            db.or_(
                Paper.abstract.is_(None),
                Paper.abstract == '',
                Paper.pdf_url.is_(None),
                Paper.pdf_url == '',
            ),
        ).order_by(Paper.metadata_next_retry_at.asc()).limit(
            Config.METADATA_RETRY_BATCH_SIZE
        ).all()]
    if paper_ids:
        _executor.submit(_update_papers, paper_ids, False)
        logger.info('已加入元数据延迟重试队列: %s 篇', len(paper_ids))
    return len(paper_ids)


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
                    db.and_(
                        Paper.source == 'dblp',
                        db.or_(
                            Paper.abstract.is_(None),
                            Paper.abstract == '',
                            Paper.pdf_url.is_(None),
                            Paper.pdf_url == '',
                        ),
                    ),
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
    if _app is None:
        return False, '元数据补全服务尚未初始化'
    with _status_lock:
        if _backfill_status['running']:
            return False, '论文元数据回填任务正在运行'
        _backfill_status.update({
            'running': True,
            'total': 0,
            'processed': 0,
            'enriched': 0,
            'abstract_enriched': 0,
            'pdf_url_enriched': 0,
            'openalex_abstract_enriched': 0,
            'openalex_pdf_url_enriched': 0,
            'unpaywall_pdf_url_enriched': 0,
            'crossref_abstract_enriched': 0,
            'crossref_pdf_url_enriched': 0,
            'arxiv_abstract_enriched': 0,
            'arxiv_pdf_url_enriched': 0,
            'publisher_abstract_enriched': 0,
            'publisher_pdf_url_enriched': 0,
            'paper_not_found': 0,
            'affiliation_missing': 0,
            'failed': 0,
            'started_at': get_beijing_time().isoformat(),
            'completed_at': None,
        })
    _executor.submit(_run_backfill)
    return True, '论文元数据回填任务已在后台启动'


def get_affiliation_backfill_status():
    with _status_lock:
        return dict(_backfill_status)
