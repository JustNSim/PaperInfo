import unittest
from unittest.mock import Mock, patch

import requests

from crawler.base import BaseCrawler


class DummyCrawler(BaseCrawler):
    def search(self, keywords, **kwargs):
        return []


class CrawlerCircuitBreakerTests(unittest.TestCase):
    def test_timeout_budget_opens_circuit_and_skips_later_requests(self):
        crawler = DummyCrawler(delay=0, timeout=1, max_retries=1)
        crawler._session.get = Mock(side_effect=requests.exceptions.Timeout('slow'))

        with patch('crawler.base.time.sleep'):
            first = crawler._make_request('https://example.test/api')
            second = crawler._make_request('https://example.test/api')

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertTrue(crawler.circuit_open)
        self.assertIn('timeout', crawler.circuit_reason)
        self.assertEqual(2, crawler._session.get.call_count)
        crawler.close()

    def test_success_does_not_open_circuit(self):
        crawler = DummyCrawler(delay=0, timeout=1, max_retries=1)
        response = Mock(status_code=200)
        crawler._session.get = Mock(return_value=response)

        result = crawler._make_request('https://example.test/api')

        self.assertIs(response, result)
        self.assertFalse(crawler.circuit_open)
        crawler.close()

    def test_retry_after_header_is_honored(self):
        crawler = DummyCrawler(delay=0, timeout=1, max_retries=1)
        limited = Mock(status_code=429, headers={'Retry-After': '7'})
        success = Mock(status_code=200, headers={})
        crawler._session.get = Mock(side_effect=[limited, success])

        with patch('crawler.base.time.sleep') as sleep:
            result = crawler._make_request('https://example.test/api')

        self.assertIs(success, result)
        sleep.assert_called_once_with(7)
        self.assertFalse(crawler.circuit_open)
        crawler.close()

    def test_503_uses_source_specific_longer_backoff(self):
        crawler = DummyCrawler(
            delay=0,
            timeout=1,
            max_retries=1,
            server_retry_after_default=10,
        )
        unavailable = Mock(status_code=503, headers={})
        success = Mock(status_code=200, headers={})
        crawler._session.get = Mock(side_effect=[unavailable, success])

        with patch('crawler.base.time.sleep') as sleep:
            result = crawler._make_request('https://example.test/api')

        self.assertIs(success, result)
        sleep.assert_called_once_with(10)
        self.assertFalse(crawler.circuit_open)
        crawler.close()


if __name__ == '__main__':
    unittest.main()
