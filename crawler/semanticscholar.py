"""
Semantic Scholar API 爬虫
使用 Semantic Scholar Graph API 获取论文信息
"""
import logging
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime

from .base import BaseCrawler

logger = logging.getLogger(__name__)


class SemanticScholarCrawler(BaseCrawler):
    """Semantic Scholar API 爬虫"""

    S2_API_URL = 'https://api.semanticscholar.org/graph/v1'
    # Semantic Scholar 速率限制: 每5分钟100次请求（有API key）
    # 使用更保守的间隔：3秒一次请求，确保不超限
    DEFAULT_DELAY = 3.0
    # 每次请求最多返回的论文数
    MAX_RESULTS_PER_REQUEST = 100
    # 最低相关性分数
    MIN_RELEVANCE_SCORE = 10

    def __init__(self, delay: float = DEFAULT_DELAY, timeout: int = 30, max_results: int = 100):
        super().__init__(delay=delay, timeout=timeout)
        self.max_results = max_results

    def search(self, keywords: List[str], venues: List[str] = None,
               from_year: Optional[int] = None, to_year: Optional[int] = None,
               **kwargs) -> List[Dict[str, Any]]:
        """
        搜索 Semantic Scholar 论文

        Args:
            keywords: 搜索关键词列表
            venues: 会议/期刊列表（用于过滤）
            from_year: 起始年份
            to_year: 结束年份
            **kwargs: 其他参数

        Returns:
            论文信息列表
        """
        all_papers = []

        # 构建查询字符串：使用 OR 逻辑组合关键词
        query = ' OR '.join(keywords)

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

        self._wait_for_rate_limit()

        try:
            # 发送搜索请求
            papers = self._search_papers(query, self.max_results)
            all_papers.extend(papers)

            logger.info(f"从 Semantic Scholar 获取到 {len(all_papers)} 篇论文")
            return all_papers

        except requests.RequestException as e:
            logger.error(f"Semantic Scholar API 请求失败: {e}")
            return []

    def _search_papers(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """执行搜索请求"""
        params = {
            'query': query,
            'limit': min(limit, 100),
            'fields': 'paperId,title,abstract,authors,venue,year,url,publicationDate,publicationTypes'
        }

        response = requests.get(
            f'{self.S2_API_URL}/paper/search',
            params=params,
            timeout=self.timeout,
            headers={'Accept': 'application/json'}
        )
        response.raise_for_status()

        data = response.json()
        papers = []

        if 'data' not in data:
            return papers

        for item in data['data']:
            paper = self._parse_paper(item)
            if paper:
                # 计算相关性分数并过滤
                score = self._calculate_relevance_score(paper)
                if score >= self.MIN_RELEVANCE_SCORE:
                    papers.append(paper)

        return papers

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

    def _calculate_relevance_score(self, paper: Dict[str, Any]) -> int:
        """
        计算论文相关性分数

        S2 覆盖面广，需要严格过滤以避免不相关论文
        """
        score = 0
        title = paper.get('title', '').lower()
        abstract = (paper.get('abstract') or '').lower()

        # 核心关键词（高权重）
        core_keywords = {'blockchain', 'smart contract', 'cryptocurrency', 'bitcoin',
                        'ethereum', 'solidity', 'defi', 'nft', 'dao', 'zk-snark',
                        'zk-stark', 'merkle', 'byzantine', 'consensus', 'sharding'}

        # 负面关键词
        negative_keywords = {'traffic signal', 'manufacturing', 'battery', 'forecasting',
                           'recommendation system', 'social network', 'search engine',
                           'image processing', 'computer vision', 'speech recognition'}

        # 检查负面关键词
        for neg_kw in negative_keywords:
            if neg_kw in title or neg_kw in abstract:
                return 0

        # 核心关键词匹配
        for core_kw in core_keywords:
            if core_kw in title:
                score += 30
                break

        # 其他关键词匹配（标题）
        title_words = {'decentralized', 'distributed', 'protocol', 'verification',
                       'cryptography', 'encryption', 'hash', 'ledger', 'token'}
        for word in title_words:
            if word in title:
                score += 10

        # 摘要匹配
        if abstract:
            for core_kw in core_keywords:
                if core_kw in abstract:
                    score += 5
                    break

        return score

    def normalize_paper(self, raw_paper: Dict[str, Any], source: str = 's2') -> Dict[str, Any]:
        """标准化论文数据（已集成在 _parse_paper 中）"""
        return raw_paper
