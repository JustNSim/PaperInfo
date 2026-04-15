"""
arXiv API 爬虫
"""
import time
import logging
import feedparser
from typing import List, Dict, Any, Optional
from datetime import datetime

from .base import BaseCrawler

logger = logging.getLogger(__name__)


class ArxivCrawler(BaseCrawler):
    """arXiv API 爬虫"""

    ARXIV_API_URL = 'http://export.arxiv.org/api/query'
    # 关键词分批查询：每批查询的关键词数量（减少批次避免429）
    KEYWORD_BATCH_SIZE = 15
    # 每批查询获取的最大结果数
    MAX_RESULTS_PER_BATCH = 100
    # 最低相关性分数（0-100）
    MIN_RELEVANCE_SCORE = 25  # 必须包含核心关键词或多个关键词
    # arXiv 建议的请求间隔（秒）
    DEFAULT_DELAY = 4.0

    def __init__(self, delay: float = DEFAULT_DELAY, timeout: int = 30, max_results: int = 100):
        super().__init__(delay=delay, timeout=timeout)
        self.max_results = max_results

    def search(self, keywords: List[str], categories: List[str] = None,
               from_date: Optional[datetime] = None, to_date: Optional[datetime] = None,
               **kwargs) -> List[Dict[str, Any]]:
        """
        搜索 arXiv 论文（支持分批查询和时间过滤）

        优化策略：将关键词分为核心词和扩展词，优先搜索核心词，
        核心词结果足够时跳过扩展词查询，减少 API 请求次数。

        Args:
            keywords: 搜索关键词列表
            categories: arXiv 分类列表（如 cs.CR, cs.DC）
            from_date: 起始日期（只获取此日期之后发表的论文）
            to_date: 结束日期（只获取此日期之前发表的论文）
            **kwargs: 其他参数（max_results 可覆盖默认值）

        Returns:
            论文信息列表
        """
        max_results = kwargs.get('max_results', self.max_results)
        all_papers = []

        if from_date:
            logger.info(f"时间过滤: 从 {from_date.strftime('%Y-%m-%d')} 开始")
        if to_date:
            logger.info(f"时间过滤: 到 {to_date.strftime('%Y-%m-%d')} 结束")

        # 区分核心关键词和扩展关键词
        # 核心关键词：前 15 个（第一批），扩展关键词：剩余的
        core_keywords = keywords[:self.KEYWORD_BATCH_SIZE]
        extended_keywords = keywords[self.KEYWORD_BATCH_SIZE:]

        # 优先搜索核心关键词
        logger.info(f"搜索核心关键词 ({len(core_keywords)} 个)")
        core_papers = self._search_batch(core_keywords, categories, self.MAX_RESULTS_PER_BATCH,
                                         from_date=from_date, to_date=to_date)
        all_papers.extend(core_papers)
        logger.info(f"核心关键词获取 {len(core_papers)} 篇论文")

        # 只有核心词结果不足时，才查询扩展关键词
        if extended_keywords and len(all_papers) < max_results:
            remaining_quota = max_results - len(all_papers)
            batches = self._batch_keywords(extended_keywords, self.KEYWORD_BATCH_SIZE)
            logger.info(f"核心结果不足 ({len(all_papers)}/{max_results})，查询扩展关键词 ({len(extended_keywords)} 个，{len(batches)} 批)")

            for i, batch in enumerate(batches, 1):
                # 批次之间等待
                extra_wait = 2.0
                logger.debug(f"批次间额外等待 {extra_wait} 秒")
                time.sleep(extra_wait)

                logger.info(f"处理扩展关键词第 {i}/{len(batches)} 批: {batch[:3]}{'...' if len(batch) > 3 else ''}")

                batch_papers = self._search_batch(batch, categories, self.MAX_RESULTS_PER_BATCH,
                                                  from_date=from_date, to_date=to_date)

                remaining_quota = max_results - len(all_papers)
                if remaining_quota <= 0:
                    logger.info(f"已达到最大结果数限制 ({max_results})，停止查询")
                    break

                if len(batch_papers) > remaining_quota:
                    batch_papers = batch_papers[:remaining_quota]

                all_papers.extend(batch_papers)
                logger.info(f"扩展第 {i} 批获取 {len(batch_papers)} 篇论文，累计 {len(all_papers)} 篇")
        else:
            logger.info(f"无扩展关键词，跳过扩展查询（核心词获取 {len(all_papers)} 篇）")

        # 去重（按 source_id）
        unique_papers = self._deduplicate_papers(all_papers)
        logger.info(f"去重后共 {len(unique_papers)} 篇论文")

        return unique_papers

    def _batch_keywords(self, keywords: List[str], batch_size: int) -> List[List[str]]:
        """将关键词分批"""
        batches = []
        for i in range(0, len(keywords), batch_size):
            batches.append(keywords[i:i + batch_size])
        return batches

    def _search_batch(self, keywords: List[str], categories: List[str] = None,
                      max_results: int = MAX_RESULTS_PER_BATCH,
                      from_date: Optional[datetime] = None,
                      to_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """执行单批查询（只搜索标题，提高精确度）"""
        # 使用 ti: 前缀只搜索标题，避免摘要中误匹配
        keyword_query = ' OR '.join([f'ti:"{kw}"' for kw in keywords])

        if categories:
            # 使用 AND 逻辑：论文必须包含关键词 AND 属于指定分类
            cat_query = ' OR '.join([f'cat:{cat}' for cat in categories])
            query = f'({keyword_query}) AND ({cat_query})'
        else:
            query = keyword_query

        # 添加时间过滤（arXiv API 支持 submit-date 过滤）
        if from_date or to_date:
            date_filter = []
            if from_date:
                # 格式: 20240101
                date_filter.append(from_date.strftime('%Y%m%d'))
            else:
                date_filter.append('19910101')  # arXiv 起始时间

            if to_date:
                date_filter.append(to_date.strftime('%Y%m%d'))
            else:
                date_filter.append(datetime.now().strftime('%Y%m%d'))

            query += f' submit-date:[{date_filter[0]} TO {date_filter[1]}]'
            logger.debug(f"添加时间过滤: {date_filter[0]} TO {date_filter[1]}")

        logger.debug(f"arXiv 批次查询: {query}")

        params = {
            'search_query': query,
            'start': 0,
            'max_results': max_results,
            'sortBy': 'submittedDate',
            'sortOrder': 'descending'
        }

        response = self._make_request(self.ARXIV_API_URL, params=params)

        if response is None:
            logger.error("arXiv API 请求失败，所有重试均失败")
            return []

        if response.status_code != 200:
            logger.error(f"arXiv API 返回错误状态码: {response.status_code}")
            return []

        try:
            # 解析 Atom feed
            feed = feedparser.parse(response.content)
            papers = []

            for entry in feed.entries:
                paper = self._parse_entry(entry)
                if paper:
                    # 客户端再次验证时间过滤（更准确）
                    if self._within_date_range(paper, from_date, to_date):
                        # 计算相关性分数并过滤
                        score = self._calculate_relevance_score(paper, keywords)
                        if score >= self.MIN_RELEVANCE_SCORE:
                            paper['relevance_score'] = score
                            papers.append(paper)
                        else:
                            logger.debug(f"论文相关性过低 ({score})，跳过: {paper['title'][:50]}")

            logger.info(f"批次获取 {len(papers)} 篇相关性达标的论文")
            return papers

        except Exception as e:
            logger.error(f"解析 arXiv 响应失败: {e}")
            return []

    def _within_date_range(self, paper: Dict[str, Any],
                           from_date: Optional[datetime] = None,
                           to_date: Optional[datetime] = None) -> bool:
        """检查论文是否在指定时间范围内"""
        published = paper.get('published_date')
        if not published:
            return True  # 没有日期信息的论文保留

        if from_date and published < from_date:
            return False
        if to_date and published > to_date:
            return False
        return True

    def _calculate_relevance_score(self, paper: Dict[str, Any], keywords: List[str]) -> int:
        """
        计算论文与关键词的相关性分数 (0-100)

        评分规则:
        - 核心关键词 = 传入 keywords 的前 10 个，必须至少匹配一个
        - 标题中完整匹配核心关键词: +30 分
        - 标题中完整匹配其他关键词: +15 分
        - 摘要匹配: +2 分/个
        - 包含负面关键词: 返回 0 分
        """
        score = 0
        title = paper.get('title', '').lower()
        abstract = (paper.get('abstract') or '').lower()

        # 核心关键词：从传入的 keywords 动态取前 10 个
        core_keywords = {kw.lower() for kw in keywords[:10]}

        # 负面关键词（包含这些说明论文不相关）
        negative_keywords = {'traffic signal', 'manufacturing', 'battery', 'state of health',
                           'forecasting', 'prediction', 'recommendation system',
                           'social network', 'information retrieval', 'search engine'}

        # 检查负面关键词（扣分）
        for neg_kw in negative_keywords:
            if neg_kw in title or neg_kw in abstract:
                logger.debug(f"包含负面关键词 '{neg_kw}'，跳过: {title[:50]}")
                return 0  # 直接返回 0 分

        # 检查是否包含核心关键词
        has_core = False
        for core_kw in core_keywords:
            if core_kw in title:
                has_core = True
                score += 30  # 核心关键词高分
                break

        # 如果没有核心关键词，检查是否有多个一般关键词
        if not has_core:
            keyword_count = 0
            for kw in keywords:
                kw_lower = kw.lower()
                if kw_lower in title:
                    keyword_count += 1
                    score += 15
            # 如果没有核心关键词，需要至少 2 个关键词匹配
            if keyword_count < 2:
                logger.debug(f"没有核心关键词且匹配关键词少于2个，跳过: {title[:50]}")
                return 0

        # 摘要匹配（低分）
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower in abstract:
                score += 2

        return score

    def _deduplicate_papers(self, papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 source_id 去重，保留最新的版本"""
        seen = {}
        for paper in papers:
            source_id = paper.get('source_id')
            if source_id:
                # 如果已存在，比较版本号，保留较新的
                if source_id in seen:
                    existing = seen[source_id]
                    # 检查版本号 (arXiv ID 格式: abs/1234.5678v5)
                    existing_ver = self._extract_version(existing.get('source_id', ''))
                    current_ver = self._extract_version(source_id)
                    if current_ver > existing_ver:
                        seen[source_id] = paper
                else:
                    seen[source_id] = paper
            else:
                # 没有 source_id 的论文使用 title 去重
                title = paper.get('title', '')
                if title and title not in seen:
                    seen[title] = paper
        return list(seen.values())

    def _extract_version(self, source_id: str) -> int:
        """从 arXiv source_id 提取版本号"""
        if not source_id:
            return 0
        # 格式: 1234.5678v5 或 abs/1234.5678v5
        parts = source_id.split('v')
        if len(parts) > 1:
            try:
                return int(parts[-1])
            except ValueError:
                return 0
        return 1  # 默认版本 1

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
