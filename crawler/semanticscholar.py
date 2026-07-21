"""
Semantic Scholar API 爬虫
使用 Semantic Scholar Graph API 获取论文信息
支持多 API Key 轮换以规避限流
"""
import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime

from .base import BaseCrawler

logger = logging.getLogger(__name__)


class SemanticScholarCrawler(BaseCrawler):
    """Semantic Scholar API 爬虫"""

    S2_API_URL = 'https://api.semanticscholar.org/graph/v1'
    DEFAULT_DELAY = 5.0
    MAX_RESULTS_PER_REQUEST = 100
    MIN_RELEVANCE_SCORE = 20

    def __init__(self, delay: float = DEFAULT_DELAY, timeout: int = 30,
                 max_results: int = 100, api_keys: List[str] = None,
                 max_retries: int = 2):
        super().__init__(
            delay=delay,
            timeout=timeout,
            max_retries=max_retries,
            retry_after_default=10,
        )
        self.max_results = max_results
        self._api_keys = api_keys or []
        self._key_index = 0

    def _get_api_key(self) -> Optional[str]:
        """获取下一个 API Key（轮换）"""
        if not self._api_keys:
            return None
        key = self._api_keys[self._key_index % len(self._api_keys)]
        self._key_index += 1
        return key

    def search(self, keywords: List[str], venues: List[str] = None,
               from_year: Optional[int] = None, to_year: Optional[int] = None,
               core_keywords: List[str] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        搜索 Semantic Scholar 论文

        Args:
            keywords: 搜索关键词列表
            venues: 会议/期刊列表（用于过滤）
            from_year: 起始年份
            to_year: 结束年份
            core_keywords: 核心关键词列表（用于相关性评分）
            **kwargs: 其他参数

        Returns:
            论文信息列表
        """
        # 如果没有提供核心关键词，从 keywords 中提取前 5 个作为核心
        if core_keywords is None:
            core_keywords = keywords[:5]

        all_papers = []

        # 构建查询字符串：使用 OR 逻辑组合关键词
        query = ' OR '.join(keywords[:20])  # 限制关键词数量

        # 添加 venue 过滤
        if venues:
            venue_query = ' OR '.join([f'venue:"{v}"' for v in venues[:5]])  # 限制 venue 数量
            query = f'({query}) AND ({venue_query})'

        # 添加年份过滤
        if from_year or to_year:
            year_parts = []
            if from_year:
                year_parts.append(str(from_year))
            else:
                year_parts.append('1900')
            if to_year:
                year_parts.append(str(to_year))
            else:
                year_parts.append(str(datetime.now().year))
            query += f' year:{year_parts[0]}-{year_parts[1]}'

        logger.info(f"Semantic Scholar 查询: {query[:100]}...")

        papers = self._search_papers(query, self.max_results, core_keywords)
        all_papers.extend(papers)

        logger.info(f"从 Semantic Scholar 获取到 {len(all_papers)} 篇论文")
        return all_papers

    def _search_papers(self, query: str, limit: int, core_keywords: List[str] = None) -> List[Dict[str, Any]]:
        """执行搜索请求

        Args:
            query: 查询字符串
            limit: 最大结果数
            core_keywords: 核心关键词列表
        """
        params = {
            'query': query,
            'limit': min(limit, 100),
            'fields': 'paperId,title,abstract,authors,venue,year,url,publicationDate,publicationTypes'
        }

        headers = {'Accept': 'application/json'}
        api_key = self._get_api_key()
        if api_key:
            headers['x-api-key'] = api_key

        response = self._make_request(
            f'{self.S2_API_URL}/paper/search',
            params=params,
            headers=headers
        )

        if response is None:
            logger.error("Semantic Scholar API 请求失败，所有重试均失败")
            return []

        if response.status_code != 200:
            logger.error(f"Semantic Scholar API 返回错误状态码: {response.status_code}")
            return []

        try:
            data = response.json()
            papers = []

            if 'data' not in data:
                return papers

            for item in data['data']:
                paper = self._parse_paper(item)
                if paper:
                    # 计算相关性分数并过滤
                    score = self._calculate_relevance_score(paper, core_keywords)
                    if score >= self.MIN_RELEVANCE_SCORE:
                        papers.append(paper)

            return papers

        except Exception as e:
            logger.error(f"解析 Semantic Scholar 响应失败: {e}")
            return []

    def _parse_paper(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """解析单篇论文"""
        try:
            # 提取作者
            authors = []
            if 'authors' in item and item['authors']:
                for author in item['authors']:
                    name = author.get('name', '')
                    if name:
                        authors.append(name)

            # 提取 venue
            venue = None
            if 'venue' in item:
                venue = item['venue']

            # 提取年份
            year = item.get('year')

            # 提取发布日期
            published_date = None
            pub_date_str = item.get('publicationDate') or item.get('publicationDate')
            if pub_date_str:
                try:
                    published_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass

            # 构建 URL
            url = item.get('url')
            if not url and item.get('paperId'):
                url = f"https://www.semanticscholar.org/paper/{item['paperId']}"

            # PDF URL（Semantic Scholar 可能提供）
            pdf_url = None
            if 'openAccessPdf' in item:
                pdf_url = item['openAccessPdf'].get('url')

            return {
                'title': item.get('title', ''),
                'authors': authors,
                'abstract': item.get('abstract'),
                'source': 's2',  # Semantic Scholar
                'source_id': item.get('paperId'),
                'year': year,
                'venue': venue,
                'url': url,
                'pdf_url': pdf_url,
                'published_date': published_date
            }

        except Exception as e:
            logger.warning(f"解析 Semantic Scholar 条目失败: {e}")
            return None

    def _calculate_relevance_score(self, paper: Dict[str, Any], core_keywords: List[str] = None) -> int:
        """
        计算论文相关性分数

        Args:
            paper: 论文数据
            core_keywords: 核心关键词列表（从领域配置传入）
        """
        score = 0
        title = paper.get('title', '').lower()
        abstract = (paper.get('abstract') or '').lower()

        if not core_keywords:
            # 没有关键词时无法评估，直接通过
            return self.MIN_RELEVANCE_SCORE

        core_set = {kw.lower() for kw in core_keywords}

        # 负面关键词（只保留真正通用的噪音词）
        negative_keywords = {'traffic signal', 'manufacturing', 'battery', 'state of health'}

        for neg_kw in negative_keywords:
            if neg_kw in title or neg_kw in abstract:
                return 0

        # 核心关键词匹配标题（高权重）
        for core_kw in core_set:
            if core_kw in title:
                score += 30
                break

        # 核心关键词匹配摘要
        if abstract:
            for core_kw in core_set:
                if core_kw in abstract:
                    score += 5
                    break

        return score

    def normalize_paper(self, raw_paper: Dict[str, Any], source: str = 's2') -> Dict[str, Any]:
        """标准化论文数据（已集成在 _parse_paper 中）"""
        return raw_paper
