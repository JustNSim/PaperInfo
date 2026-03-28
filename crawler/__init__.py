"""
PaperInfo 爬虫模块
"""
from .arxiv import ArxivCrawler
from .dblp import DBLPCrawler
from .semanticscholar import SemanticScholarCrawler

__all__ = ['ArxivCrawler', 'DBLPCrawler', 'SemanticScholarCrawler']
