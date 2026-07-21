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

    def __init__(self, delay: float = 3.0, timeout: int = 30,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 retry_after_default: int = 10):
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
        self.retry_after_default = retry_after_default
        self._last_request_time = 0
        self._consecutive_failures = 0  # 连续失败计数，用于自适应延迟
        self._circuit_open = False
        self._circuit_reason = None
        self._session = self._create_session()

    @property
    def circuit_open(self) -> bool:
        """本轮更新中该来源是否已连续失败并停止继续请求。"""
        return self._circuit_open

    @property
    def circuit_reason(self) -> Optional[str]:
        return self._circuit_reason

    def _open_circuit(self, reason: str):
        self._circuit_open = True
        self._circuit_reason = reason
        logger.warning("本轮更新暂停继续请求该来源: %s", reason)

    def _create_session(self) -> requests.Session:
        """创建请求会话（重试由 _make_request 手动处理，禁用 urllib3 层重试避免双重重试放大）"""
        session = requests.Session()

        # 禁用 urllib3 层重试，由 _make_request 的手动重试层统一处理
        # 这样避免 urllib3 重试 + 手动重试 = 最多 9 次实际请求的问题
        retry_strategy = Retry(
            total=0,
            raise_on_status=False,
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
        """确保遵守请求频率限制（自适应：连续失败时动态增加延迟）"""
        # 自适应延迟：连续失败时指数增长，上限 30 秒
        effective_delay = min(self.delay * (2 ** self._consecutive_failures), 30)
        if effective_delay > self.delay:
            logger.debug(f"自适应延迟: {effective_delay:.1f} 秒 (连续失败 {self._consecutive_failures} 次)")

        elapsed = time.time() - self._last_request_time
        if elapsed < effective_delay:
            sleep_time = effective_delay - elapsed
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
        if self._circuit_open:
            logger.info("来源熔断已开启，跳过请求: %s", url)
            return None

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

                # 处理 429 错误（频率限制）— 不增加 consecutive_failures，429 已有显式退避
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    if retry_after:
                        try:
                            wait_time = int(retry_after)
                        except ValueError:
                            wait_time = 60
                    else:
                        # 没有 Retry-After 时使用来源指定的保守退避。
                        wait_time = min(60, (2 ** attempt) * self.retry_after_default)

                    if attempt < self.max_retries:
                        logger.warning(f"收到 429 错误，等待 {wait_time} 秒后重试 (尝试 {attempt + 1}/{self.max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"达到最大重试次数，放弃请求: {url}")
                        self._open_circuit(f'HTTP 429 after {attempt + 1} attempts')
                        return response

                # 处理其他服务器错误 — 不增加 consecutive_failures，已有显式退避
                if response.status_code >= 500:
                    if attempt < self.max_retries:
                        wait_time = (2 ** attempt) * 3
                        logger.warning(f"服务器错误 {response.status_code}，等待 {wait_time} 秒后重试")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"达到最大重试次数，服务器错误: {response.status_code}")
                        self._open_circuit(
                            f'HTTP {response.status_code} after {attempt + 1} attempts'
                        )
                        return response

                # 请求成功，重置连续失败计数
                self._consecutive_failures = 0
                return response

            except requests.exceptions.ConnectionError as e:
                last_exception = e
                self._consecutive_failures += 1
                if attempt < self.max_retries:
                    wait_time = (2 ** attempt) * 5
                    logger.warning(f"连接错误: {e}，等待 {wait_time} 秒后重试 (尝试 {attempt + 1}/{self.max_retries})")
                    time.sleep(wait_time)
                    # 重新创建会话
                    self._session = self._create_session()
                else:
                    logger.error(f"连接错误，达到最大重试次数: {e}")
                    self._open_circuit(
                        f'connection error after {attempt + 1} attempts'
                    )

            except requests.exceptions.Timeout as e:
                last_exception = e
                self._consecutive_failures += 1
                if attempt < self.max_retries:
                    wait_time = (2 ** attempt) * 3
                    logger.warning(f"请求超时，等待 {wait_time} 秒后重试 (尝试 {attempt + 1}/{self.max_retries})")
                    time.sleep(wait_time)
                else:
                    logger.error(f"请求超时，达到最大重试次数: {e}")
                    self._open_circuit(
                        f'timeout after {attempt + 1} attempts'
                    )

            except requests.RequestException as e:
                last_exception = e
                self._consecutive_failures += 1
                logger.error(f"请求异常: {e}")
                if attempt < self.max_retries:
                    time.sleep(5)
                else:
                    self._open_circuit(
                        f'request error after {attempt + 1} attempts'
                    )
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
