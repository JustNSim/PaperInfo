import os
import tempfile
import unittest
from datetime import datetime

from crawler.dblp import DBLPCrawler


@unittest.skipUnless(
    os.environ.get('RUN_DBLP_LIVE_TESTS') == '1',
    '设置 RUN_DBLP_LIVE_TESTS=1 后运行真实 DBLP venue 数据冒烟测试',
)
class DBLPLiveSmokeTests(unittest.TestCase):
    def test_blockchain_ccs_query_returns_current_records(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            crawler = DBLPCrawler(
                delay=2,
                timeout=30,
                max_results=10,
                max_retries=1,
                cache_dir=cache_dir,
            )
            try:
                current_year = datetime.now().year
                papers = crawler.search(
                    keywords=['blockchain'],
                    venues=['CCS'],
                    from_year=current_year - 3,
                    to_year=current_year,
                )
                self.assertIsNone(
                    crawler.source_failure_reason,
                    crawler.source_failure_reason,
                )
                self.assertTrue(
                    papers,
                    'DBLP stream 返回成功但没有得到近期 CCS blockchain 论文',
                )
                self.assertTrue(all(paper['source'] == 'dblp' for paper in papers))
            finally:
                crawler.close()


if __name__ == '__main__':
    unittest.main()
