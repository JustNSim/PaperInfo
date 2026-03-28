"""
爬虫基类
"""
import time
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class BaseCrawler(ABC):
    """爬虫基类"""

    def __init__(self, delay: float = 3.0, timeout: int = 30):
        """
        初始化爬虫

        Args:
            delay: 请求间隔（秒）
            timeout: 请求超时时间（秒）
        """
        self.delay = delay
        self.timeout = timeout
        self._last_request_time = 0

    def _wait_for_rate_limit(self):
        """确保遵守请求频率限制"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_time = time.time()

    @abstractmethod
    def search(self, keywords: List[str], **kwargs) -> List[Dict[str, Any]]:
        """
        搜索论文

        Args:
            keywords: 搜索关键词列表
            **kwargs: 其他搜索参数

        Returns:
            论文信息列表
        """
        pass

    def normalize_paper(self, raw_paper: Dict[str, Any], source: str) -> Dict[str, Any]:
        """
        标准化论文数据格式

        Args:
            raw_paper: 原始论文数据
            source: 数据源名称

        Returns:
            标准化后的论文数据
        """
        raise NotImplementedError
