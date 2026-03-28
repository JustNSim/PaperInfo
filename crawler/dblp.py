"""
DBLP API 爬虫
"""
import logging
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime

from .base import BaseCrawler

logger = logging.getLogger(__name__)


class DBLPCrawler(BaseCrawler):
    """DBLP API 爬虫"""

    DBLP_API_URL = 'https://dblp.org/search/publ/api'
    # 每个 venue 获取的最大结果数
    MAX_RESULTS_PER_VENUE = 50

    def __init__(self, delay: float = 3.0, timeout: int = 30, max_results: int = 100):
        super().__init__(delay=delay, timeout=timeout)
        self.max_results = max_results

    def search(self, keywords: List[str], venues: List[str] = None,
               from_year: Optional[int] = None, to_year: Optional[int] = None,
               **kwargs) -> List[Dict[str, Any]]:
        """
        搜索 DBLP 论文（按 venue 分别查询，支持时间过滤）

        Args:
            keywords: 搜索关键词列表
            venues: 会议/期刊列表（如 'IEEE S&P', 'ACM CCS'）
            from_year: 起始年份（只获取此年份及之后的论文）
            to_year: 结束年份（只获取此年份及之前的论文）
            **kwargs: 其他参数

        Returns:
            论文信息列表
        """
        all_papers = []

        if venues:
            # 按 venue 分别查询（服务端过滤）
            logger.info(f"按 {len(venues)} 个 venue 分别查询 DBLP")
            for venue in venues:
                papers = self._search_by_venue(keywords, venue, from_year, to_year)
                all_papers.extend(papers)
                logger.info(f"Venue '{venue}' 获取 {len(papers)} 篇论文")

                # 检查是否达到总数限制
                if len(all_papers) >= self.max_results:
                    all_papers = all_papers[:self.max_results]
                    break
        else:
            # 没有 venue 限制，使用通用查询
            all_papers = self._search_general(keywords, from_year, to_year)

        # 去重
        unique_papers = self._deduplicate_papers(all_papers)
        logger.info(f"DBLP 去重后共 {len(unique_papers)} 篇论文")

        return unique_papers

    def _search_by_venue(self, keywords: List[str], venue: str,
                         from_year: Optional[int] = None,
                         to_year: Optional[int] = None) -> List[Dict[str, Any]]:
        """按指定 venue 搜索"""
        self._wait_for_rate_limit()

        try:
            # 构建查询：关键词 + venue 过滤
            # 限制关键词数量以避免查询过长
            query_keywords = keywords[:10]  # 使用前 10 个关键词
            keyword_query = ' OR '.join([f'"{kw}"' for kw in query_keywords])

            # 使用 DBLP 的 venue: 语法进行服务端过滤
            query = f'({keyword_query}) venue:{venue}'
            params = {
                'q': query,
                'format': 'json',
                'h': self.MAX_RESULTS_PER_VENUE,
                'c': 0
            }

            # 添加年份过滤
            if from_year or to_year:
                year_filter = []
                if from_year:
                    year_filter.append(str(from_year))
                else:
                    year_filter.append('1900')
                if to_year:
                    year_filter.append(str(to_year))
                else:
                    year_filter.append(str(datetime.now().year))
                params['year'] = f'{year_filter[0]}:{year_filter[1]}'

            logger.debug(f"DBLP venue 查询: {query[:100]}...")

            response = requests.get(
                self.DBLP_API_URL,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()

            data = response.json()
            papers = []

            # 检查是否有结果
            if 'result' not in data:
                logger.debug(f"DBLP venue '{venue}' 无结果")
                return papers

            hits_data = data['result'].get('hits', {})
            if not hits_data:
                logger.debug(f"DBLP venue '{venue}' hits 为空")
                return papers

            # 获取 hit 列表
            hits = hits_data.get('hit')
            if not hits:
                logger.debug(f"DBLP venue '{venue}' 没有命中")
                return papers

            if not isinstance(hits, list):
                hits = [hits]

            for hit in hits:
                paper = self._parse_venue_entry(hit, venue, from_year, to_year)
                if paper:
                    papers.append(paper)

            return papers

        except requests.RequestException as e:
            logger.error(f"DBLP venue '{venue}' 查询失败: {e}")
            return []

    def _search_general(self, keywords: List[str],
                       from_year: Optional[int] = None,
                       to_year: Optional[int] = None) -> List[Dict[str, Any]]:
        """通用查询（不指定 venue）"""
        self._wait_for_rate_limit()

        try:
            # 构建组合查询：关键词1 OR 关键词2 OR ...
            # 使用 DBLP 的 OR 语法
            query = ' OR '.join([f'"{kw}"' for kw in keywords])

            params = {
                'q': query,
                'format': 'json',
                'h': self.max_results,  # 一次请求获取最大结果数
                'c': 0  # 从第一条开始
            }

            # 添加年份过滤（DBLP API 支持 year 参数）
            if from_year or to_year:
                year_filter = []
                if from_year:
                    year_filter.append(str(from_year))
                else:
                    year_filter.append('1900')
                if to_year:
                    year_filter.append(str(to_year))
                else:
                    year_filter.append(str(datetime.now().year))

                params['year'] = f'{year_filter[0]}:{year_filter[1]}'
                logger.info(f"DBLP 年份过滤: {params['year']}")

            logger.info(f"DBLP 组合搜索查询: {query[:200]}...")  # 截断日志避免过长

            response = requests.get(
                self.DBLP_API_URL,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()

            data = response.json()

            # 解析结果
            all_papers = []
            if 'result' in data:
                hits_data = data['result'].get('hits', {})
                if hits_data:
                    hits = hits_data.get('hit')
                    if hits:
                        if not isinstance(hits, list):
                            hits = [hits]

                        for hit in hits:
                            paper = self._parse_entry(hit, from_year, to_year)
                            if paper:
                                all_papers.append(paper)

            logger.info(f"从 DBLP 获取到 {len(all_papers)} 篇论文")
            return all_papers

        except requests.RequestException as e:
            logger.error(f"DBLP API 请求失败: {e}")
            return []

    def _parse_entry(self, hit: Dict,
                     from_year: Optional[int] = None,
                     to_year: Optional[int] = None) -> Dict[str, Any]:
        """解析单个论文条目（不需要 venue 过滤，已服务端过滤）"""
        try:
            info = hit.get('info', {})

            # 提取作者
            authors = []
            if 'authors' in info:
                authors_data = info['authors']['author']
                if not isinstance(authors_data, list):
                    authors_data = [authors_data]
                authors = [a.get('text', '') for a in authors_data]

            # 提取 venue（信息保留，不再过滤）
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
                    return None  # 年份太早
                if to_year and year > to_year:
                    return None  # 年份太晚

            # 提取 URL
            url = info.get('url', '')
            ee = info.get('ee', '')  # 电子版链接
            final_url = ee if ee else url

            # DBLP 通常不直接提供 PDF，需要从会议页面获取
            pdf_url = None

            # 提取发布日期（DBLP 只有年份）
            published_date = None
            if year:
                try:
                    published_date = datetime(year, 1, 1)
                except ValueError:
                    pass

            # 提取来源 ID（DBLP URL 的最后一部分）
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
                'pdf_url': pdf_url,
                'published_date': published_date
            }

        except Exception as e:
            logger.warning(f"解析 DBLP 条目失败: {e}")
            return None

    def _parse_venue_entry(self, hit: Dict, venue: str,
                           from_year: Optional[int] = None,
                           to_year: Optional[int] = None) -> Dict[str, Any]:
        """解析来自 venue 查询的论文条目（已服务端过滤，不需要再次验证 venue）"""
        # 复用 _parse_entry，但确保 venue 字段正确
        paper = self._parse_entry(hit, from_year, to_year)
        if paper and not paper.get('venue'):
            # 如果 API 返回的 venue 为空，使用查询的 venue
            paper['venue'] = venue
        return paper

    def _deduplicate_papers(self, papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 source_id 去重"""
        seen = {}
        for paper in papers:
            source_id = paper.get('source_id')
            if source_id and source_id not in seen:
                seen[source_id] = paper
            elif not source_id:
                # 没有 source_id 的使用 title 去重
                title = paper.get('title', '')
                if title and title not in seen:
                    seen[title] = paper
        return list(seen.values())

    def normalize_paper(self, raw_paper: Dict[str, Any], source: str = 'dblp') -> Dict[str, Any]:
        """标准化论文数据（已集成在 _parse_entry 中）"""
        return raw_paper
