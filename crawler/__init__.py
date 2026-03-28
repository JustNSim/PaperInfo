"""
PaperInfo 爬虫模块
"""
from .arxiv import ArxivCrawler
from .dblp import DBLPCrawler

__all__ = ['ArxivCrawler', 'DBLPCrawler']
