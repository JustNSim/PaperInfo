"""
DBLP API 爬虫
"""
import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime

from .base import BaseCrawler

logger = logging.getLogger(__name__)


class DBLPCrawler(BaseCrawler):
    """DBLP API 爬虫"""

    DBLP_API_URL = 'https://dblp.org/search/publ/api'
    # 每个 venue 获取的最大结果数
    MAX_RESULTS_PER_VENUE = 50
    # DBLP 请求间隔（秒）
    DEFAULT_DELAY = 3.0
    # 管道合并的 venue 每批最多数量
    PIPE_BATCH_SIZE = 15

    def __init__(self, delay: float = DEFAULT_DELAY, timeout: int = 30, max_results: int = 100):
        super().__init__(delay=delay, timeout=timeout)
        self.max_results = max_results

    def search(self, keywords: List[str], venues: List[str] = None,
               from_year: Optional[int] = None, to_year: Optional[int] = None,
               **kwargs) -> List[Dict[str, Any]]:
        """
        搜索 DBLP 论文（优化 venue 查询策略，减少请求数）

        策略：
        - 单词 venue（如 ICSE, CCS）用管道符合并为一次查询: venue:ICSE|CCS|NDSS
        - 含空格的 venue（如 "IEEE S&P"）单独查询
        - 分别查询避免 DBLP API 语法限制

        Args:
            keywords: 搜索关键词列表
            venues: 会议/期刊列表
            from_year: 起始年份
            to_year: 结束年份
            **kwargs: 其他参数

        Returns:
            论文信息列表
        """
        all_papers = []

        if venues:
            # 分离单词 venue 和含空格 venue
            simple_venues = [v for v in venues if ' ' not in v]
            complex_venues = [v for v in venues if ' ' in v]

            logger.info(f"DBLP 查询: {len(venues)} 个 venue ({len(simple_venues)} 简单, {len(complex_venues)} 含空格)")

            # 1. 用管道符合并所有简单 venue（少量请求）
            if simple_venues:
                simple_batches = self._batch_venues(simple_venues, self.PIPE_BATCH_SIZE)
                for i, batch in enumerate(simple_batches, 1):
                    papers = self._search_by_venue_pipe(keywords, batch, from_year, to_year)
                    all_papers.extend(papers)
                    logger.info(f"简单 venue 批次 {i}/{len(simple_batches)} ({len(batch)} 个) 获取 {len(papers)} 篇论文")

                    if len(all_papers) >= self.max_results:
                        break

            # 2. 含空格的 venue 逐个查询
            if complex_venues and len(all_papers) < self.max_results:
                logger.info(f"查询 {len(complex_venues)} 个含空格 venue（逐个查询）")
                for i, venue in enumerate(complex_venues, 1):
                    papers = self._search_by_venue(keywords, venue, from_year, to_year)
                    all_papers.extend(papers)
                    logger.debug(f"Venue '{venue}' 获取 {len(papers)} 篇 ({i}/{len(complex_venues)})")

                    if len(all_papers) >= self.max_results:
                        break
        else:
            # 没有 venue 限制，使用通用查询
            all_papers = self._search_general(keywords, from_year, to_year)

        # 检查总数限制
        if len(all_papers) > self.max_results:
            all_papers = all_papers[:self.max_results]

        # 去重
        unique_papers = self._deduplicate_papers(all_papers)
        logger.info(f"DBLP 去重后共 {len(unique_papers)} 篇论文")

        return unique_papers

    def _batch_venues(self, venues: List[str], batch_size: int = PIPE_BATCH_SIZE) -> List[List[str]]:
        """将 venues 按 batch_size 分组"""
        return [venues[i:i + batch_size] for i in range(0, len(venues), batch_size)]

    def _search_by_venue_pipe(self, keywords: List[str], venues: List[str],
                               from_year: Optional[int] = None,
                               to_year: Optional[int] = None) -> List[Dict[str, Any]]:
        """用管道符合并多个单词 venue 查询（DBLP 支持 venue:A|B|C 语法，不加引号）"""
        try:
            query_keywords = keywords[:10]
            keyword_query = ' OR '.join([f'"{kw}"' for kw in query_keywords])

            # 管道符合并 venue（不加引号，不含空格的 venue 名才用此方式）
            venue_part = '|'.join(venues)
            query = f'({keyword_query}) venue:{venue_part}'

            max_h = min(self.MAX_RESULTS_PER_VENUE * len(venues), 1000)
            params = {
                'q': query,
                'format': 'json',
                'h': max_h,
                'c': 0
            }

            if from_year or to_year:
                y1 = str(from_year) if from_year else '1900'
                y2 = str(to_year) if to_year else str(datetime.now().year)
                params['year'] = f'{y1}:{y2}'

            logger.debug(f"DBLP 管道查询 ({len(venues)} 个 venue): {query[:100]}...")

            response = self._make_request(self.DBLP_API_URL, params=params)

            if response is None:
                logger.warning(f"DBLP 管道查询失败（{len(venues)} 个 venue）")
                return []

            if response.status_code != 200:
                logger.warning(f"DBLP 管道查询返回错误状态码: {response.status_code}")
                return []

            data = response.json()
            papers = []

            hits = self._extract_hits(data)
            for hit in hits:
                paper = self._parse_entry(hit, from_year, to_year)
                if paper:
                    papers.append(paper)

            return papers

        except Exception as e:
            logger.error(f"DBLP 管道 venue 查询失败: {e}")
            return []

    def _search_by_venue(self, keywords: List[str], venue: str,
                         from_year: Optional[int] = None,
                         to_year: Optional[int] = None) -> List[Dict[str, Any]]:
        """按指定 venue 搜索（用于含空格的 venue 名，不加引号）"""
        try:
            query_keywords = keywords[:10]
            keyword_query = ' OR '.join([f'"{kw}"' for kw in query_keywords])

            # 不加引号（经测试 venue:"xxx" 会返回 0 结果）
            query = f'({keyword_query}) venue:{venue}'
            params = {
                'q': query,
                'format': 'json',
                'h': self.MAX_RESULTS_PER_VENUE,
                'c': 0
            }

            if from_year or to_year:
                y1 = str(from_year) if from_year else '1900'
                y2 = str(to_year) if to_year else str(datetime.now().year)
                params['year'] = f'{y1}:{y2}'

            logger.debug(f"DBLP venue 查询: {query[:100]}...")

            response = self._make_request(self.DBLP_API_URL, params=params)

            if response is None:
                logger.warning(f"DBLP venue '{venue}' 查询失败：所有重试均失败")
                return []

            if response.status_code != 200:
                logger.warning(f"DBLP venue '{venue}' 返回错误状态码: {response.status_code}")
                return []

            data = response.json()
            papers = []

            hits = self._extract_hits(data)
            for hit in hits:
                paper = self._parse_entry(hit, from_year, to_year)
                if paper:
                    if not paper.get('venue'):
                        paper['venue'] = venue
                    papers.append(paper)

            return papers

        except Exception as e:
            logger.error(f"DBLP venue '{venue}' 查询失败: {e}")
            return []

    def _search_general(self, keywords: List[str],
                       from_year: Optional[int] = None,
                       to_year: Optional[int] = None) -> List[Dict[str, Any]]:
        """通用查询（不指定 venue）"""
        try:
            query = ' OR '.join([f'"{kw}"' for kw in keywords])

            params = {
                'q': query,
                'format': 'json',
                'h': self.max_results,
                'c': 0
            }

            if from_year or to_year:
                y1 = str(from_year) if from_year else '1900'
                y2 = str(to_year) if to_year else str(datetime.now().year)
                params['year'] = f'{y1}:{y2}'
                logger.info(f"DBLP 年份过滤: {params['year']}")

            logger.info(f"DBLP 组合搜索查询: {query[:200]}...")

            response = self._make_request(self.DBLP_API_URL, params=params)

            if response is None:
                logger.error("DBLP API 请求失败：所有重试均失败")
                return []

            if response.status_code != 200:
                logger.error(f"DBLP API 返回错误状态码: {response.status_code}")
                return []

            data = response.json()
            all_papers = []

            hits = self._extract_hits(data)
            for hit in hits:
                paper = self._parse_entry(hit, from_year, to_year)
                if paper:
                    all_papers.append(paper)

            logger.info(f"从 DBLP 获取到 {len(all_papers)} 篇论文")
            return all_papers

        except Exception as e:
            logger.error(f"DBLP API 请求失败: {e}")
            return []

    def _extract_hits(self, data: dict) -> list:
        """从 DBLP API 响应中提取 hits 列表"""
        if 'result' not in data:
            return []
        hits_data = data['result'].get('hits', {})
        if not hits_data:
            return []
        hits = hits_data.get('hit')
        if not hits:
            return []
        if not isinstance(hits, list):
            hits = [hits]
        return hits

    def _parse_entry(self, hit: Dict,
                     from_year: Optional[int] = None,
                     to_year: Optional[int] = None) -> Dict[str, Any]:
        """解析单个论文条目"""
        try:
            info = hit.get('info', {})

            # 提取作者
            authors = []
            if 'authors' in info:
                authors_data = info['authors']['author']
                if not isinstance(authors_data, list):
                    authors_data = [authors_data]
                authors = [a.get('text', '') for a in authors_data]

            # 提取 venue
            venue = info.get('venue', '')

            # 提取年份
            year = None
            if 'year' in info:
                try:
                    year = int(info['year'])
                except (ValueError, TypeError):
                    pass

            # 时间范围过滤
            if year:
                if from_year and year < from_year:
                    return None
                if to_year and year > to_year:
                    return None

            # 提取 URL
            url = info.get('url', '')
            ee = info.get('ee', '')
            final_url = ee if ee else url

            # 提取发布日期
            published_date = None
            if year:
                try:
                    published_date = datetime(year, 1, 1)
                except ValueError:
                    pass

            # 提取来源 ID
            source_id = None
            if url:
                source_id = url.rstrip('/').split('/')[-1]

            return {
                'title': info.get('title', ''),
                'authors': authors,
                'abstract': None,  # DBLP API 不提供摘要
                'source': 'dblp',
                'source_id': source_id,
                'year': year,
                'venue': venue,
                'url': final_url,
                'pdf_url': None,
                'published_date': published_date
            }

        except Exception as e:
            logger.warning(f"解析 DBLP 条目失败: {e}")
            return None

    def _deduplicate_papers(self, papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 source_id 去重"""
        seen = {}
        for paper in papers:
            source_id = paper.get('source_id')
            if source_id and source_id not in seen:
                seen[source_id] = paper
            elif not source_id:
                title = paper.get('title', '')
                if title and title not in seen:
                    seen[title] = paper
        return list(seen.values())

    def normalize_paper(self, raw_paper: Dict[str, Any], source: str = 'dblp') -> Dict[str, Any]:
        """标准化论文数据"""
        return raw_paper
