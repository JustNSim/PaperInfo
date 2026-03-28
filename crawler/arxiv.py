"""
arXiv API 爬虫
"""
import time
import logging
import requests
import feedparser
from typing import List, Dict, Any
from datetime import datetime

from .base import BaseCrawler

logger = logging.getLogger(__name__)


class ArxivCrawler(BaseCrawler):
    """arXiv API 爬虫"""

    ARXIV_API_URL = 'http://export.arxiv.org/api/query'
    # 限制关键词数量以提高查询精度
    MAX_KEYWORDS = 10

    def __init__(self, delay: float = 3.0, timeout: int = 30, max_results: int = 100):
        super().__init__(delay=delay, timeout=timeout)
        self.max_results = max_results

    def search(self, keywords: List[str], categories: List[str] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        搜索 arXiv 论文

        Args:
            keywords: 搜索关键词列表
            categories: arXiv 分类列表（如 cs.CR, cs.DC）
            **kwargs: 其他参数（max_results 可覆盖默认值）

        Returns:
            论文信息列表
        """
        max_results = kwargs.get('max_results', self.max_results)

        # 限制关键词数量以提高查询精度（取前 MAX_KEYWORDS 个最重要的关键词）
        effective_keywords = keywords[:self.MAX_KEYWORDS] if len(keywords) > self.MAX_KEYWORDS else keywords
        if len(keywords) > self.MAX_KEYWORDS:
            logger.info(f"关键词数量超过限制，使用前 {self.MAX_KEYWORDS} 个关键词（共 {len(keywords)} 个）")

        # 构建查询字符串
        keyword_query = ' OR '.join([f'all:"{kw}"' for kw in effective_keywords])

        if categories:
            # 使用 AND 逻辑：论文必须包含关键词 AND 属于指定分类
            cat_query = ' OR '.join([f'cat:{cat}' for cat in categories])
            query = f'({keyword_query}) AND ({cat_query})'
        else:
            query = keyword_query

        logger.info(f"arXiv 搜索查询: {query}")

        self._wait_for_rate_limit()

        try:
            params = {
                'search_query': query,
                'start': 0,
                'max_results': max_results,
                'sortBy': 'submittedDate',
                'sortOrder': 'descending'
            }

            response = requests.get(
                self.ARXIV_API_URL,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()

            # 解析 Atom feed
            feed = feedparser.parse(response.content)
            papers = []

            for entry in feed.entries:
                paper = self._parse_entry(entry)
                if paper:
                    papers.append(paper)

            logger.info(f"从 arXiv 获取到 {len(papers)} 篇论文")
            return papers

        except requests.RequestException as e:
            logger.error(f"arXiv API 请求失败: {e}")
            return []

    def _parse_entry(self, entry) -> Dict[str, Any]:
        """解析单个论文条目"""
        try:
            # 提取作者
            authors = []
            if hasattr(entry, 'authors'):
                authors = [author.name for author in entry.authors]
            elif 'author' in entry:
                # 某些版本的 feedparser 格式不同
                authors = [author.get('name', '') for author in entry.get('author', [])]

            # 提取 PDF 链接
            pdf_url = None
            for link in entry.get('links', []):
                if link.get('type') == 'application/pdf':
                    pdf_url = link.href
                    break

            # 提取摘要（去除多余空白）
            summary = entry.get('summary', '')
            summary = ' '.join(summary.split())

            # 提取发布日期
            published_date = None
            if 'published' in entry:
                try:
                    published_date = datetime.strptime(
                        entry.published, '%Y-%m-%dT%H:%M:%SZ'
                    )
                except ValueError:
                    try:
                        published_date = datetime.strptime(
                            entry.published, '%Y-%m-%dT%H:%M:%S.%fZ'
                        )
                    except ValueError:
                        pass

            # 提取 arXiv ID
            arxiv_id = entry.get('id', '').split('/v')[-1].split('/')[-1]
            if 'arxiv.org_abs' in arxiv_id:
                arxiv_id = arxiv_id.split('abs/')[-1]

            return {
                'title': entry.get('title', '').strip(),
                'authors': authors,
                'abstract': summary,
                'source': 'arxiv',
                'source_id': arxiv_id,
                'year': published_date.year if published_date else None,
                'venue': 'arXiv',
                'url': entry.get('id', ''),
                'pdf_url': pdf_url,
                'published_date': published_date
            }

        except Exception as e:
            logger.warning(f"解析 arXiv 条目失败: {e}")
            return None

    def normalize_paper(self, raw_paper: Dict[str, Any], source: str = 'arxiv') -> Dict[str, Any]:
        """标准化论文数据（已集成在 _parse_entry 中）"""
        return raw_paper
