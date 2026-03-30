"""
爬虫基类
"""
import time
import logging
import requests
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class BaseCrawler(ABC):
    """爬虫基类"""

    # 默认重试配置
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_BACKOFF_FACTOR = 2.0  # 指数退避因子
    DEFAULT_RETRY_STATUS_CODES = [429, 500, 502, 503, 504]

    def __init__(self, delay: float = 3.0, timeout: int = 30, max_retries: int = DEFAULT_MAX_RETRIES):
        """
        初始化爬虫

        Args:
            delay: 请求间隔（秒）
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
        """
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request_time = 0
        self._session = self._create_session()

    def _create_session(self) -> requests.Session:
        """创建带有重试机制的请求会话"""
        session = requests.Session()

        # 配置重试策略
        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=self.DEFAULT_RETRY_BACKOFF_FACTOR,
            status_forcelist=self.DEFAULT_RETRY_STATUS_CODES,
            allowed_methods=["GET", "POST"],
            raise_on_status=False,  # 不自动抛出异常，让我们手动处理
        )

        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=10,
            pool_block=False
        )

        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # 设置默认请求头
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/xml, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        })

        return session

    def _wait_for_rate_limit(self):
        """确保遵守请求频率限制"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.delay:
            sleep_time = self.delay - elapsed
            logger.debug(f"等待 {sleep_time:.1f} 秒以遵守频率限制")
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _make_request(self, url: str, params: dict = None, headers: dict = None,
                      method: str = 'GET', data: dict = None) -> Optional[requests.Response]:
        """
        发送带重试机制的 HTTP 请求

        Args:
            url: 请求 URL
            params: 查询参数
            headers: 额外的请求头
            method: 请求方法
            data: POST 数据

        Returns:
            Response 对象或 None（如果所有重试都失败）
        """
        self._wait_for_rate_limit()

        request_headers = {}
        if headers:
            request_headers.update(headers)

        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                if method.upper() == 'GET':
                    response = self._session.get(
                        url,
                        params=params,
                        headers=request_headers,
                        timeout=self.timeout
                    )
                else:
                    response = self._session.post(
                        url,
                        params=params,
                        data=data,
                        headers=request_headers,
                        timeout=self.timeout
                    )

                # 处理 429 错误（频率限制）
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    if retry_after:
                        try:
                            wait_time = int(retry_after)
                        except ValueError:
                            wait_time = 60
                    else:
                        # 指数退避
                        wait_time = min(60, (2 ** attempt) * 5)

                    if attempt < self.max_retries:
                        logger.warning(f"收到 429 错误，等待 {wait_time} 秒后重试 (尝试 {attempt + 1}/{self.max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"达到最大重试次数，放弃请求: {url}")
                        return response

                # 处理其他服务器错误
                if response.status_code >= 500:
                    if attempt < self.max_retries:
                        wait_time = (2 ** attempt) * 3
                        logger.warning(f"服务器错误 {response.status_code}，等待 {wait_time} 秒后重试")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"达到最大重试次数，服务器错误: {response.status_code}")
                        return response

                return response

            except requests.exceptions.ConnectionError as e:
                last_exception = e
                if attempt < self.max_retries:
                    wait_time = (2 ** attempt) * 5
                    logger.warning(f"连接错误: {e}，等待 {wait_time} 秒后重试 (尝试 {attempt + 1}/{self.max_retries})")
                    time.sleep(wait_time)
                    # 重新创建会话
                    self._session = self._create_session()
                else:
                    logger.error(f"连接错误，达到最大重试次数: {e}")

            except requests.exceptions.Timeout as e:
                last_exception = e
                if attempt < self.max_retries:
                    wait_time = (2 ** attempt) * 3
                    logger.warning(f"请求超时，等待 {wait_time} 秒后重试 (尝试 {attempt + 1}/{self.max_retries})")
                    time.sleep(wait_time)
                else:
                    logger.error(f"请求超时，达到最大重试次数: {e}")

            except requests.RequestException as e:
                last_exception = e
                logger.error(f"请求异常: {e}")
                if attempt < self.max_retries:
                    time.sleep(5)
                else:
                    break

        return None

    def close(self):
        """关闭请求会话"""
        if self._session:
            self._session.close()

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
