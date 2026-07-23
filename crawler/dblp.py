"""DBLP venue stream crawler with persistent local caching."""

from __future__ import annotations

import html
import io
import json
import logging
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

from .base import BaseCrawler

logger = logging.getLogger(__name__)


def infer_dblp_pdf_url(urls: Iterable[str]) -> Optional[str]:
    """Infer direct PDFs only for publishers with deterministic URL schemes."""
    for raw_url in urls or []:
        url = str(raw_url or '').strip()
        if not url.lower().startswith(('http://', 'https://')):
            continue
        parsed = urlparse(url)
        host = parsed.netloc.casefold()
        path = parsed.path
        if path.casefold().endswith('.pdf'):
            return url
        doi_match = re.search(r'(10\.1145/[^?#]+)', path, re.IGNORECASE)
        if doi_match and host in {'doi.org', 'dx.doi.org', 'dl.acm.org'}:
            doi = doi_match.group(1).rstrip('/')
            return f'https://dl.acm.org/doi/epdf/{doi}'
        if host == 'aclanthology.org' and path.strip('/'):
            return urlunparse(parsed._replace(path=path.rstrip('/') + '.pdf'))
        if host == 'proceedings.mlr.press' and path.casefold().endswith('.html'):
            match = re.fullmatch(r'/(v\d+)/([^/]+)\.html', path, re.IGNORECASE)
            if match:
                volume, slug = match.groups()
                return (
                    f'https://raw.githubusercontent.com/mlresearch/{volume}/'
                    f'main/assets/{slug}/{slug}.pdf'
                )
        if host in {'jmlr.org', 'www.jmlr.org'} and path.casefold().endswith('.html'):
            match = re.fullmatch(
                r'/papers/v(\d+)/([^/]+)\.html', path, re.IGNORECASE
            )
            if match:
                volume, slug = match.groups()
                return (
                    f'https://jmlr.org/papers/volume{volume}/'
                    f'{slug}/{slug}.pdf'
                )
        if host == 'papers.nips.cc' and path.casefold().endswith('.html'):
            pdf_path = path.replace('/hash/', '/file/')
            pdf_path = re.sub(
                r'-Abstract(?:-Conference)?\.html$',
                '-Paper-Conference.pdf',
                pdf_path,
                flags=re.IGNORECASE,
            )
            if pdf_path != path:
                return urlunparse(parsed._replace(
                    scheme='https', path=pdf_path
                ))
    return None


class DBLPCrawler(BaseCrawler):
    """Read stable DBLP venue streams and filter their records locally."""

    DEFAULT_BASE_URLS = (
        'https://dblp.org',
        'https://dblp.uni-trier.de',
        'https://dblp.dagstuhl.de',
    )
    DEFAULT_DELAY = 3.0
    DEFAULT_CACHE_TTL_HOURS = 24.0
    DEFAULT_STALE_CACHE_DAYS = 30.0
    CACHE_VERSION = 1
    PUBLICATION_TAGS = {'article', 'inproceedings', 'incollection'}

    # DBLP stream keys are stable identifiers and do not always match a venue's
    # common abbreviation. Values are tuples because venue histories can span
    # more than one stream (for example NIPS/NeurIPS).
    VENUE_STREAMS: Dict[str, Tuple[str, ...]] = {
        'ieee s&p': ('conf/sp',),
        'ieee sp': ('conf/sp',),
        'sp': ('conf/sp',),
        'ccs': ('conf/ccs',),
        'usenix security': ('conf/uss',),
        'ndss': ('conf/ndss',),
        'icse': ('conf/icse',),
        'fse': ('conf/sigsoft',),
        'esec/fse': ('conf/sigsoft',),
        'esec-fse': ('conf/sigsoft',),
        'ase': ('conf/kbse',),
        'issta': ('conf/issta',),
        'oopsla': ('conf/oopsla',),
        'ieee tse': ('journals/tse',),
        'tse': ('journals/tse',),
        'acm tosem': ('journals/tosem',),
        'tosem': ('journals/tosem',),
        'aaai': ('conf/aaai',),
        'ijcai': ('conf/ijcai',),
        'acl': ('conf/acl',),
        'www': ('conf/www',),
        'icml': ('conf/icml',),
        'neurips': ('conf/nips',),
        'nips': ('conf/nips',),
        'iclr': ('conf/iclr',),
        'emnlp': ('conf/emnlp',),
        'naacl': ('conf/naacl',),
        'coling': ('conf/coling',),
        'aamas': ('conf/ifaamas',),
        'kdd': ('conf/kdd',),
        'sigir': ('conf/sigir',),
        'chi': ('conf/chi',),
        'jmlr': ('journals/jmlr',),
        'ieee tpami': ('journals/pami',),
        'tpami': ('journals/pami',),
    }

    def __init__(
        self,
        delay: float = DEFAULT_DELAY,
        timeout: int = 30,
        max_results: int = 100,
        max_retries: int = 1,
        base_urls: Optional[Iterable[str]] = None,
        stream_base_urls: Optional[Iterable[str]] = None,
        cache_dir: Optional[str] = None,
        cache_ttl_hours: float = DEFAULT_CACHE_TTL_HOURS,
        stale_cache_days: float = DEFAULT_STALE_CACHE_DAYS,
        venue_streams: Optional[Dict[str, Sequence[str]]] = None,
    ):
        configured_base_urls = base_urls or stream_base_urls or self.DEFAULT_BASE_URLS
        self.base_urls = tuple(
            str(url).rstrip('/')
            for url in configured_base_urls
            if str(url).strip()
        )
        if not self.base_urls:
            raise ValueError('DBLP 至少需要一个基础地址')

        # DBLP cache fallback is handled per stream. A failed refresh must not
        # stop the crawler from reading other fresh/stale cached streams.
        super().__init__(
            delay=delay,
            timeout=timeout,
            max_retries=max_retries,
            retry_after_default=30,
            server_retry_after_default=10,
            circuit_failure_threshold=1_000_000,
            open_circuit_on_rate_limit=False,
        )
        self._session.headers.update({
            'Accept': 'application/xml, text/xml;q=0.9, */*;q=0.1',
            'User-Agent': 'PaperInfo/1.0 (DBLP venue stream cache)',
        })

        self.max_results = max(1, int(max_results))
        self.cache_dir = Path(cache_dir or 'data/dblp_cache').resolve()
        self.cache_ttl_seconds = max(0.0, float(cache_ttl_hours) * 3600)
        self.stale_cache_seconds = max(
            self.cache_ttl_seconds,
            float(stale_cache_days) * 86400,
        )
        self.venue_streams = dict(self.VENUE_STREAMS)
        for venue, streams in (venue_streams or {}).items():
            normalized = self._normalize_venue(venue)
            if isinstance(streams, str):
                streams = (streams,)
            self.venue_streams[normalized] = tuple(
                self._normalize_stream_key(item) for item in streams if item
            )

        self._preferred_base_url = self.base_urls[0]
        self._stream_memory_cache: Dict[Tuple[str, Optional[int], Optional[int]], List[Dict[str, Any]]] = {}
        self._refresh_attempted = set()
        self._network_suspended_reason: Optional[str] = None
        self._missing_streams: Dict[str, str] = {}
        self._unknown_venues = set()
        self._planned_streams = set()
        self.cache_stats = {
            'fresh': 0,
            'refreshed': 0,
            'revalidated': 0,
            'stale': 0,
            'missing': 0,
        }

    @property
    def source_failure_reason(self) -> Optional[str]:
        """Return a failure only when requested venue data was unavailable."""
        failures = []
        if self._unknown_venues:
            failures.append(
                'unmapped venues: ' + ', '.join(sorted(self._unknown_venues))
            )
        if self._missing_streams:
            failures.append(
                'unavailable streams: ' + ', '.join(sorted(self._missing_streams))
            )
        return '; '.join(failures) or None

    @staticmethod
    def _normalize_venue(venue: str) -> str:
        return ' '.join(str(venue or '').casefold().split())

    @staticmethod
    def _normalize_stream_key(stream_key: str) -> str:
        value = str(stream_key or '').strip().strip('/')
        if value.startswith('streams/'):
            value = value[len('streams/'):]
        if value.endswith('.xml'):
            value = value[:-4]
        return value

    @staticmethod
    def _clean_keywords(keywords: Iterable[str]) -> List[str]:
        seen = set()
        cleaned = []
        for keyword in keywords or []:
            value = ' '.join(str(keyword or '').split())
            normalized = value.casefold()
            if value and normalized not in seen:
                seen.add(normalized)
                cleaned.append(value)
        return cleaned

    def _resolve_stream_keys(self, venue: str) -> Tuple[str, ...]:
        return tuple(self.venue_streams.get(self._normalize_venue(venue), ()))

    def prepare_domains(self, domains: Iterable[Any]):
        """Prepare one in-memory plan so overlapping streams are reused."""
        self._stream_memory_cache.clear()
        self._refresh_attempted.clear()
        self._network_suspended_reason = None
        self._missing_streams.clear()
        self._unknown_venues.clear()
        planned_streams = set()
        venues = set()
        for domain in domains or []:
            for venue in getattr(domain, 'ccf_venues', []) or []:
                venues.add(self._normalize_venue(venue))
                planned_streams.update(self._resolve_stream_keys(venue))
        self._planned_streams = planned_streams
        logger.info(
            'DBLP venue stream 计划已准备: %s 个 venue，%s 个唯一 stream',
            len(venues), len(planned_streams),
        )

    def search(
        self,
        keywords: List[str],
        venues: List[str] = None,
        from_year: Optional[int] = None,
        to_year: Optional[int] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Load venue streams, then filter year and title keywords locally."""
        unique_venues = list(dict.fromkeys(
            str(venue).strip() for venue in (venues or []) if str(venue).strip()
        ))
        if not unique_venues:
            logger.warning('DBLP stream 模式需要配置 venue，本次未执行通用搜索')
            return []

        all_papers = []
        logger.info('DBLP 本地过滤 %s 个 venue stream', len(unique_venues))
        for index, venue in enumerate(unique_venues, 1):
            stream_keys = self._resolve_stream_keys(venue)
            if not stream_keys:
                self._unknown_venues.add(venue)
                logger.error("DBLP venue '%s' 尚未配置 stream key，已跳过", venue)
                continue

            candidates = []
            for stream_key in stream_keys:
                stream_papers = self._get_stream_candidates(
                    stream_key, venue, from_year, to_year
                )
                if stream_papers is not None:
                    candidates.extend(stream_papers)
            candidates = self._deduplicate_papers(candidates)
            papers = [
                paper for paper in candidates
                if self._paper_matches_keywords(paper, keywords)
            ]
            all_papers.extend(papers)
            logger.info(
                "DBLP venue %s/%s '%s': 本地年份候选 %s 篇，关键词匹配 %s 篇",
                index, len(unique_venues), venue, len(candidates), len(papers),
            )
            if len(all_papers) >= self.max_results:
                break

        unique_papers = self._deduplicate_papers(all_papers)[:self.max_results]
        logger.info(
            'DBLP 本地过滤完成: %s 篇；缓存 fresh=%s refreshed=%s '
            'revalidated=%s stale=%s missing=%s',
            len(unique_papers),
            self.cache_stats['fresh'], self.cache_stats['refreshed'],
            self.cache_stats['revalidated'], self.cache_stats['stale'],
            self.cache_stats['missing'],
        )
        return unique_papers

    def _ordered_base_urls(self) -> List[str]:
        preferred_index = self.base_urls.index(
            self._preferred_base_url
        )
        return list(
            self.base_urls[preferred_index:]
            + self.base_urls[:preferred_index]
        )

    def _request_resource_with_failover(
        self, resource_path: str, headers: Optional[dict] = None
    ):
        failures = []
        retryable_failure = False
        normalized_path = str(resource_path or '').strip().strip('/')
        if normalized_path.endswith('.xml'):
            normalized_path = normalized_path[:-4]
        for base_url in self._ordered_base_urls():
            url = f'{base_url}/{normalized_path}.xml'
            response = self._make_request(url, headers=headers)
            if response is not None and response.status_code in (200, 304):
                if base_url != self._preferred_base_url:
                    logger.warning('DBLP venue 数据已切换到官方镜像: %s', base_url)
                self._preferred_base_url = base_url
                return response, url

            if response is None:
                reason = 'network error'
                retryable_failure = True
            else:
                reason = f'HTTP {response.status_code}'
                retryable_failure = retryable_failure or response.status_code in (
                    429, 500, 502, 503, 504
                )
            failures.append(f'{url}: {reason}')
            logger.warning('DBLP venue 数据端点失败，尝试下一个镜像: %s', failures[-1])

        reason = 'all DBLP venue data endpoints failed; ' + '; '.join(failures)
        if retryable_failure:
            # Avoid multiplying an upstream outage by every configured venue.
            self._network_suspended_reason = reason
        return None, reason

    def _cache_paths(self, resource_path: str) -> Tuple[Path, Path]:
        safe_name = str(resource_path).strip().strip('/').replace('/', '__')
        return (
            self.cache_dir / f'{safe_name}.xml',
            self.cache_dir / f'{safe_name}.json',
        )

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            with path.open('r', encoding='utf-8') as handle:
                value = json.load(handle)
                return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[float]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError):
            return None

    def _cache_age_seconds(self, xml_path: Path, metadata: dict) -> float:
        timestamp = self._parse_timestamp(metadata.get('validated_at'))
        if timestamp is None:
            try:
                timestamp = xml_path.stat().st_mtime
            except OSError:
                return float('inf')
        return max(0.0, datetime.now(timezone.utc).timestamp() - timestamp)

    def _conditional_headers(self, metadata: dict) -> dict:
        headers = {}
        if metadata.get('etag'):
            headers['If-None-Match'] = metadata['etag']
        if metadata.get('last_modified'):
            headers['If-Modified-Since'] = metadata['last_modified']
        return headers

    def _metadata_for_response(
        self, resource_path: str, response, source_url: str, previous: dict = None
    ) -> dict:
        previous = previous or {}
        headers = getattr(response, 'headers', {}) or {}
        return {
            'cache_version': self.CACHE_VERSION,
            'resource_path': str(resource_path).strip().strip('/'),
            'validated_at': datetime.now(timezone.utc).isoformat(),
            'etag': headers.get('ETag') or previous.get('etag'),
            'last_modified': (
                headers.get('Last-Modified') or previous.get('last_modified')
            ),
            'source_url': source_url or previous.get('source_url'),
        }

    def _atomic_write_bytes(self, path: Path, data: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='wb', delete=False, dir=str(path.parent),
                prefix=f'.{path.name}.', suffix='.tmp',
            ) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(path))
        finally:
            if temporary and temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def _write_metadata(self, path: Path, metadata: dict):
        payload = json.dumps(
            metadata, ensure_ascii=False, indent=2, sort_keys=True
        ).encode('utf-8')
        self._atomic_write_bytes(path, payload)

    def _read_cache_bytes(self, xml_path: Path) -> Optional[bytes]:
        try:
            return xml_path.read_bytes()
        except OSError:
            return None

    def _get_resource_bytes(
        self, resource_path: str, cache_ttl_seconds: Optional[float] = None
    ) -> Optional[bytes]:
        normalized_path = str(resource_path or '').strip().strip('/')
        xml_path, metadata_path = self._cache_paths(normalized_path)
        metadata = self._read_json(metadata_path)
        cache_bytes = self._read_cache_bytes(xml_path)
        cache_age = self._cache_age_seconds(xml_path, metadata)
        ttl_seconds = (
            self.cache_ttl_seconds
            if cache_ttl_seconds is None
            else max(0.0, float(cache_ttl_seconds))
        )
        cache_usable = (
            cache_bytes is not None and cache_age <= self.stale_cache_seconds
        )

        if cache_bytes is not None and cache_age <= ttl_seconds:
            try:
                ET.fromstring(cache_bytes)
                self.cache_stats['fresh'] += 1
                logger.info('DBLP venue 数据持久缓存命中: %s', normalized_path)
                return cache_bytes
            except ET.ParseError as exc:
                logger.warning('DBLP venue 数据缓存损坏，将重新下载 %s: %s', normalized_path, exc)
                cache_usable = False

        response = None
        source_url = None
        failure_reason = self._network_suspended_reason
        if normalized_path not in self._refresh_attempted and not failure_reason:
            self._refresh_attempted.add(normalized_path)
            response, source_url = self._request_resource_with_failover(
                normalized_path,
                headers=self._conditional_headers(metadata) if cache_bytes else None,
            )

        if response is not None and response.status_code == 304 and cache_bytes:
            try:
                ET.fromstring(cache_bytes)
                self._write_metadata(
                    metadata_path,
                    self._metadata_for_response(
                        normalized_path, response, source_url, metadata
                    ),
                )
                self.cache_stats['revalidated'] += 1
                logger.info('DBLP venue 数据缓存未变化: %s', normalized_path)
                return cache_bytes
            except ET.ParseError as exc:
                failure_reason = f'cached XML invalid after HTTP 304: {exc}'

        if response is not None and response.status_code == 200:
            content = bytes(response.content or b'')
            try:
                ET.fromstring(content)
                self._atomic_write_bytes(xml_path, content)
                self._write_metadata(
                    metadata_path,
                    self._metadata_for_response(
                        normalized_path, response, source_url, metadata
                    ),
                )
                self.cache_stats['refreshed'] += 1
                logger.info(
                    'DBLP venue 数据已刷新: %s (%s bytes)',
                    normalized_path, len(content),
                )
                return content
            except ET.ParseError as exc:
                failure_reason = f'invalid DBLP venue XML: {exc}'
                logger.error('DBLP venue XML 无效 %s: %s', normalized_path, exc)

        if cache_usable and cache_bytes is not None:
            try:
                ET.fromstring(cache_bytes)
                self.cache_stats['stale'] += 1
                logger.warning(
                    'DBLP venue 数据刷新失败，使用旧缓存: %s（%.1f 小时）',
                    normalized_path, cache_age / 3600,
                )
                return cache_bytes
            except ET.ParseError as exc:
                failure_reason = f'stale cache invalid: {exc}'

        failure_reason = (
            failure_reason
            or self._network_suspended_reason
            or f'no usable cache for {normalized_path}'
        )
        self._missing_streams[normalized_path] = failure_reason
        self.cache_stats['missing'] += 1
        logger.error('DBLP venue 数据不可用且无可用缓存 %s: %s', normalized_path, failure_reason)
        return None

    @staticmethod
    def _year_in_range(
        year: Optional[int], from_year: Optional[int], to_year: Optional[int]
    ) -> bool:
        if year is None:
            return False
        return not ((from_year and year < from_year) or (to_year and year > to_year))

    def _discover_toc_resources(
        self,
        index_content: bytes,
        stream_key: str,
        from_year: Optional[int],
        to_year: Optional[int],
    ) -> List[Tuple[str, int]]:
        root = ET.fromstring(index_content)
        resources: List[Tuple[str, int]] = []

        # Conference indexes contain proceedings records for their main venue.
        for proceedings in root.iter('proceedings'):
            year_text = self._element_text(proceedings.find('year'))
            try:
                year = int(year_text)
            except (TypeError, ValueError):
                continue
            if not self._year_in_range(year, from_year, to_year):
                continue
            url = self._element_text(proceedings.find('url'))
            if url.endswith('.html'):
                resources.append((url[:-5], year))

        if resources:
            return list(dict.fromkeys(resources))

        # Journal indexes are compact lists of volume links. Restrict links to
        # the stream's own path so related venues/workshops are not included.
        expected_prefix = f'db/{self._normalize_stream_key(stream_key)}/'
        for reference in root.iter('ref'):
            href = str(reference.attrib.get('href') or '')
            text = self._element_text(reference)
            match = re.search(r'\b(19|20)\d{2}\b', text)
            if not match:
                continue
            year = int(match.group(0))
            if not self._year_in_range(year, from_year, to_year):
                continue
            if (
                href.startswith(expected_prefix)
                and href.endswith('.html')
                and not href.endswith('/index.html')
            ):
                resources.append((href[:-5], year))
        return list(dict.fromkeys(resources))

    def _get_stream_candidates(
        self,
        stream_key: str,
        venue: str,
        from_year: Optional[int],
        to_year: Optional[int],
    ) -> Optional[List[Dict[str, Any]]]:
        normalized_key = self._normalize_stream_key(stream_key)
        memory_key = (normalized_key, from_year, to_year)
        if memory_key in self._stream_memory_cache:
            logger.info('DBLP stream 内存缓存命中: %s', normalized_key)
            return list(self._stream_memory_cache[memory_key])

        index_resource = f'db/{normalized_key}/index'
        index_content = self._get_resource_bytes(index_resource)
        if index_content is None:
            return None
        try:
            toc_resources = self._discover_toc_resources(
                index_content, normalized_key, from_year, to_year
            )
        except ET.ParseError as exc:
            reason = f'invalid venue index XML: {exc}'
            self._missing_streams[index_resource] = reason
            logger.error('DBLP venue 索引解析失败 %s: %s', normalized_key, exc)
            return None

        papers = []
        current_year = datetime.now().year
        for resource_path, resource_year in toc_resources:
            # Archived TOCs change very rarely; keep them fresh for one year.
            ttl = (
                self.cache_ttl_seconds
                if resource_year >= current_year
                else max(self.cache_ttl_seconds, 365 * 86400)
            )
            content = self._get_resource_bytes(resource_path, ttl)
            if content is None:
                continue
            try:
                papers.extend(
                    self._parse_stream_xml(content, venue, from_year, to_year)
                )
            except ET.ParseError as exc:
                self._missing_streams[resource_path] = f'invalid TOC XML: {exc}'
                logger.error('DBLP 年度目录解析失败 %s: %s', resource_path, exc)

        papers = self._deduplicate_papers(papers)
        self._stream_memory_cache[memory_key] = papers
        logger.info(
            'DBLP stream %s: 发现 %s 个年度目录，本地年份候选 %s 篇',
            normalized_key, len(toc_resources), len(papers),
        )
        return list(papers)

    @staticmethod
    def _element_text(element: Optional[ET.Element]) -> str:
        if element is None:
            return ''
        return html.unescape(''.join(element.itertext())).strip()

    def _parse_stream_xml(
        self,
        content: bytes,
        venue: str,
        from_year: Optional[int],
        to_year: Optional[int],
    ) -> List[Dict[str, Any]]:
        papers = []
        for _, element in ET.iterparse(io.BytesIO(content), events=('end',)):
            tag = element.tag.rsplit('}', 1)[-1]
            if tag not in self.PUBLICATION_TAGS:
                continue
            paper = self._parse_stream_record(
                element, venue, from_year, to_year
            )
            if paper:
                papers.append(paper)
            element.clear()
        return self._deduplicate_papers(papers)

    def _parse_stream_record(
        self,
        record: ET.Element,
        venue: str,
        from_year: Optional[int],
        to_year: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        year_text = self._element_text(record.find('year'))
        try:
            year = int(year_text)
        except (TypeError, ValueError):
            year = None
        if (from_year or to_year) and year is None:
            return None
        if from_year and year < from_year:
            return None
        if to_year and year > to_year:
            return None

        title = self._element_text(record.find('title'))
        source_id = str(record.attrib.get('key') or '').strip()
        if not title or not source_id:
            return None

        authors = [
            self._element_text(author)
            for author in record.findall('author')
            if self._element_text(author)
        ]
        ee_values = [
            self._element_text(ee)
            for ee in record.findall('ee')
            if self._element_text(ee)
        ]
        doi = self._element_text(record.find('doi')) or None
        if not doi:
            for candidate in ee_values:
                match = re.search(
                    r'(10\.\d{4,9}/[^?#\s]+)', candidate, re.IGNORECASE
                )
                if match:
                    doi = match.group(1).rstrip('.,;)')
                    break

        record_venue = (
            self._element_text(record.find('booktitle'))
            or self._element_text(record.find('journal'))
            or venue
        )
        return {
            'title': title,
            'authors': authors,
            'abstract': None,
            'source': 'dblp',
            'source_id': source_id,
            'doi': doi,
            'year': year,
            'venue': record_venue,
            'url': ee_values[0] if ee_values else f'https://dblp.org/rec/{source_id}',
            'pdf_url': infer_dblp_pdf_url(ee_values),
            'published_date': datetime(year, 1, 1) if year else None,
        }

    @staticmethod
    def _paper_matches_keywords(
        paper: Dict[str, Any], keywords: Iterable[str]
    ) -> bool:
        cleaned_keywords = DBLPCrawler._clean_keywords(keywords)
        if not cleaned_keywords:
            return True
        title = re.sub(r'<[^>]+>', ' ', str(paper.get('title') or ''))
        title = re.sub(r'[^\w]+', ' ', title.casefold()).strip()
        title_tokens = title.split()
        for keyword in cleaned_keywords:
            normalized = re.sub(r'[^\w]+', ' ', keyword.casefold()).strip()
            if not normalized:
                continue
            keyword_tokens = normalized.split()
            if all(
                any(title_token.startswith(term) for title_token in title_tokens)
                for term in keyword_tokens
            ):
                return True
        return False

    # Kept for compatibility with existing parser-level tests and callers that
    # already have a DBLP search hit. Normal updates no longer use search API.
    def _parse_entry(
        self,
        hit: Dict,
        from_year: Optional[int] = None,
        to_year: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            info = hit.get('info', {})
            year = int(info['year']) if info.get('year') else None
            if from_year and year is not None and year < from_year:
                return None
            if to_year and year is not None and year > to_year:
                return None
            authors_data = info.get('authors', {}).get('author', [])
            if not isinstance(authors_data, list):
                authors_data = [authors_data]
            authors = [
                author.get('text', '') if isinstance(author, dict) else str(author)
                for author in authors_data
            ]
            ee = info.get('ee', '')
            ee_values = ee if isinstance(ee, list) else ([ee] if ee else [])
            ee_values = [
                value.get('text') or value.get('#text')
                if isinstance(value, dict) else value
                for value in ee_values
            ]
            ee_values = [value for value in ee_values if value]
            doi = info.get('doi')
            if not doi:
                for candidate in ee_values:
                    match = re.search(
                        r'(10\.\d{4,9}/[^?#\s]+)', str(candidate), re.IGNORECASE
                    )
                    if match:
                        doi = match.group(1).rstrip('.,;)')
                        break
            url = str(info.get('url') or '')
            source_id = url.split('/rec/', 1)[-1] if '/rec/' in url else url.rstrip('/').split('/')[-1]
            return {
                'title': info.get('title', ''),
                'authors': authors,
                'abstract': None,
                'source': 'dblp',
                'source_id': source_id,
                'doi': doi,
                'year': year,
                'venue': info.get('venue', ''),
                'url': ee_values[0] if ee_values else url,
                'pdf_url': infer_dblp_pdf_url(ee_values),
                'published_date': datetime(year, 1, 1) if year else None,
            }
        except (TypeError, ValueError, AttributeError) as exc:
            logger.warning('解析 DBLP 条目失败: %s', exc)
            return None

    @staticmethod
    def _deduplicate_papers(
        papers: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        seen = {}
        for paper in papers:
            source_id = paper.get('source_id')
            key = source_id or paper.get('title')
            if key and key not in seen:
                seen[key] = paper
        return list(seen.values())

    def normalize_paper(
        self, raw_paper: Dict[str, Any], source: str = 'dblp'
    ) -> Dict[str, Any]:
        return raw_paper
