"""
DBLP API 爬虫
"""
import logging
import requests
from typing import List, Dict, Any
from datetime import datetime

from .base import BaseCrawler

logger = logging.getLogger(__name__)


class DBLPCrawler(BaseCrawler):
    """DBLP API 爬虫"""

    DBLP_API_URL = 'https://dblp.org/search/publ/api'

    def __init__(self, delay: float = 3.0, timeout: int = 30, max_results: int = 100):
        super().__init__(delay=delay, timeout=timeout)
        self.max_results = max_results

    def search(self, keywords: List[str], venues: List[str] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        搜索 DBLP 论文

        Args:
            keywords: 搜索关键词列表
            venues: 会议/期刊列表（如 'IEEE S&P', 'ACM CCS'）
            **kwargs: 其他参数

        Returns:
            论文信息列表
        """
        all_papers = []

        # 对每个关键词进行搜索
        for keyword in keywords:
            self._wait_for_rate_limit()

            try:
                # 构建查询
                query = keyword
                params = {
                    'q': query,
                    'format': 'json',
                    'h': self.max_results // len(keywords),  # 每个关键词的结果数
                    'c': 0  # 从第一条开始
                }

                logger.info(f"DBLP 搜索查询: {query}")

                response = requests.get(
                    self.DBLP_API_URL,
                    params=params,
                    timeout=self.timeout
                )
                response.raise_for_status()

                data = response.json()

                # 解析结果
                if 'result' in data and 'hits' in data['result']:
                    hits = data['result']['hits']['hit']
                    if not isinstance(hits, list):
                        hits = [hits]

                    for hit in hits:
                        paper = self._parse_entry(hit, venues)
                        if paper:
                            all_papers.append(paper)

            except requests.RequestException as e:
                logger.error(f"DBLP API 请求失败: {e}")
                continue

        logger.info(f"从 DBLP 获取到 {len(all_papers)} 篇论文")
        return all_papers

    def _parse_entry(self, hit: Dict, venues: List[str] = None) -> Dict[str, Any]:
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

            # 过滤 venue
            venue = info.get('venue', '')
            if venues:
                # 检查是否匹配指定 venue
                venue_match = any(v.lower() in venue.lower() for v in venues)
                if not venue_match and venue:
                    # 如果有 venue 但不匹配，跳过
                    # 如果没有 venue 信息，保留
                    return None

            # 提取年份
            year = None
            if 'year' in info:
                try:
                    year = int(info['year'])
                except (ValueError, TypeError):
                    pass

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

    def normalize_paper(self, raw_paper: Dict[str, Any], source: str = 'dblp') -> Dict[str, Any]:
        """标准化论文数据（已集成在 _parse_entry 中）"""
        return raw_paper
