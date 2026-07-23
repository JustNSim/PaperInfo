import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from crawler.dblp import DBLPCrawler, infer_dblp_pdf_url


def make_stream_xml():
    return b'''<?xml version="1.0" encoding="UTF-8"?>
<dblp>
  <inproceedings key="conf/ccs/Smart26">
    <author>Alice Example</author>
    <author>Bob Example</author>
    <title>Repairing Smart Contract Vulnerabilities</title>
    <year>2026</year>
    <booktitle>CCS</booktitle>
    <ee>https://doi.org/10.1234/smart.2026</ee>
  </inproceedings>
  <inproceedings key="conf/ccs/Agent25">
    <author>Carol Example</author>
    <title>Long-Term Memory for Autonomous Agents</title>
    <year>2025</year>
    <booktitle>CCS</booktitle>
  </inproceedings>
  <inproceedings key="conf/ccs/Old23">
    <author>Old Author</author>
    <title>Repairing Smart Contracts in the Past</title>
    <year>2023</year>
    <booktitle>CCS</booktitle>
  </inproceedings>
  <proceedings key="conf/ccs/2026">
    <title>CCS 2026 Proceedings</title>
    <year>2026</year>
  </proceedings>
</dblp>'''


def make_conference_index_xml():
    return b'''<?xml version="1.0" encoding="UTF-8"?>
<bht key="db/conf/ccs/index.bht" title="CCS">
  <h2>CCS 2026</h2>
  <dblpcites><r><proceedings key="conf/ccs/2026">
    <title>CCS 2026</title>
    <year>2026</year>
    <url>db/conf/ccs/ccs2026.html</url>
  </proceedings></r></dblpcites>
  <h2>CCS 2023</h2>
  <dblpcites><r><proceedings key="conf/ccs/2023">
    <title>CCS 2023</title>
    <year>2023</year>
    <url>db/conf/ccs/ccs2023.html</url>
  </proceedings></r></dblpcites>
</bht>'''


def make_response(content=b'', status=200, headers=None):
    return Mock(status_code=status, headers=headers or {}, content=content)


class DBLPCrawlerTests(unittest.TestCase):
    def make_crawler(self, cache_dir, **kwargs):
        options = {
            'delay': 0,
            'max_retries': 0,
            'cache_dir': cache_dir,
            'base_urls': ['https://primary.test'],
        }
        options.update(kwargs)
        return DBLPCrawler(**options)

    def test_stream_filters_year_and_keywords_locally(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            crawler = self.make_crawler(cache_dir)
            crawler._request_resource_with_failover = Mock(side_effect=[
                (make_response(make_conference_index_xml()),
                 'https://primary.test/db/conf/ccs/index.xml'),
                (make_response(make_stream_xml()),
                 'https://primary.test/db/conf/ccs/ccs2026.xml'),
            ])

            papers = crawler.search(
                ['smart contract'], ['CCS'], from_year=2024, to_year=2026
            )

            self.assertEqual(
                ['Repairing Smart Contract Vulnerabilities'],
                [paper['title'] for paper in papers],
            )
            self.assertEqual('conf/ccs/Smart26', papers[0]['source_id'])
            self.assertEqual('10.1234/smart.2026', papers[0]['doi'])
            self.assertEqual(['Alice Example', 'Bob Example'], papers[0]['authors'])
            self.assertEqual(2, crawler.cache_stats['refreshed'])
            crawler.close()

    def test_journal_index_discovers_only_requested_year_volumes(self):
        index_xml = b'''<bht key="db/journals/pami/index.bht">
          <ul>
            <li><ref href="db/journals/pami/pami48.html">Volume 48: 2026</ref></li>
            <li><ref href="db/journals/pami/pami47.html">Volume 47: 2025</ref></li>
            <li><ref href="db/journals/other/other48.html">Related: 2026</ref></li>
            <li><ref href="db/journals/pami/pami45.html">Volume 45: 2023</ref></li>
          </ul>
        </bht>'''
        with tempfile.TemporaryDirectory() as cache_dir:
            crawler = self.make_crawler(cache_dir)

            resources = crawler._discover_toc_resources(
                index_xml, 'journals/pami', 2024, 2026
            )

            self.assertEqual([
                ('db/journals/pami/pami48', 2026),
                ('db/journals/pami/pami47', 2025),
            ], resources)
            crawler.close()

    def test_known_open_access_landing_pages_infer_direct_pdf(self):
        cases = {
            'https://aclanthology.org/2026.acl-long.41/':
                'https://aclanthology.org/2026.acl-long.41.pdf',
            'https://dl.acm.org/doi/10.1145/3691620.3695555':
                'https://dl.acm.org/doi/epdf/10.1145/3691620.3695555',
            'https://doi.org/10.1145/3691620.3695555':
                'https://dl.acm.org/doi/epdf/10.1145/3691620.3695555',
            'https://proceedings.mlr.press/v235/lee24c.html':
                'https://raw.githubusercontent.com/mlresearch/v235/main/assets/lee24c/lee24c.pdf',
            'https://jmlr.org/papers/v26/24-0043.html':
                'https://jmlr.org/papers/volume26/24-0043/24-0043.pdf',
            'http://papers.nips.cc/paper_files/paper/2024/hash/abc-Abstract-Conference.html':
                'https://papers.nips.cc/paper_files/paper/2024/file/abc-Paper-Conference.pdf',
        }
        for landing_page, expected in cases.items():
            with self.subTest(landing_page=landing_page):
                self.assertEqual(expected, infer_dblp_pdf_url([landing_page]))

        # OpenReview exposes a PDF-like route, but it commonly rejects direct
        # downloads without a browser session, so do not advertise it as a
        # reliable downloadable file.
        self.assertIsNone(infer_dblp_pdf_url([
            'https://openreview.net/forum?id=CjXaMI2kUH'
        ]))

    def test_persistent_cache_is_reused_by_new_crawler_instance(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            first = self.make_crawler(cache_dir, cache_ttl_hours=24)
            first._request_resource_with_failover = Mock(side_effect=[
                (make_response(make_conference_index_xml()),
                 'https://primary.test/db/conf/ccs/index.xml'),
                (make_response(make_stream_xml(), headers={'ETag': '"toc-v1"'}),
                 'https://primary.test/db/conf/ccs/ccs2026.xml'),
            ])
            first.search(['smart contract'], ['CCS'], 2024, 2026)
            first.close()

            second = self.make_crawler(cache_dir, cache_ttl_hours=24)
            second._request_resource_with_failover = Mock()
            papers = second.search(['agent memory'], ['CCS'], 2024, 2026)

            self.assertEqual(
                ['Long-Term Memory for Autonomous Agents'],
                [paper['title'] for paper in papers],
            )
            second._request_resource_with_failover.assert_not_called()
            self.assertEqual(2, second.cache_stats['fresh'])
            second.close()

    def test_stale_cache_is_conditionally_revalidated_with_304(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            first = self.make_crawler(cache_dir)
            response_headers = {
                'ETag': '"stream-v1"',
                'Last-Modified': 'Wed, 22 Jul 2026 10:00:00 GMT',
            }
            first._request_resource_with_failover = Mock(side_effect=[
                (make_response(make_conference_index_xml(), headers=response_headers),
                 'https://primary.test/db/conf/ccs/index.xml'),
                (make_response(make_stream_xml(), headers=response_headers),
                 'https://primary.test/db/conf/ccs/ccs2026.xml'),
            ])
            first.search(['smart contract'], ['CCS'], 2024, 2026)
            first.close()

            second = self.make_crawler(cache_dir, cache_ttl_hours=0)
            second._request_resource_with_failover = Mock(side_effect=[
                (make_response(status=304, headers={'ETag': '"stream-v1"'}),
                 'https://primary.test/db/conf/ccs/index.xml'),
                (make_response(status=304, headers={'ETag': '"stream-v1"'}),
                 'https://primary.test/db/conf/ccs/ccs2026.xml'),
            ])
            papers = second.search(['smart contract'], ['CCS'], 2024, 2026)

            self.assertEqual(1, len(papers))
            for call in second._request_resource_with_failover.call_args_list:
                request_headers = call.kwargs['headers']
                self.assertEqual('"stream-v1"', request_headers['If-None-Match'])
                self.assertIn('If-Modified-Since', request_headers)
            self.assertEqual(2, second.cache_stats['revalidated'])
            second.close()

    def test_refresh_failure_uses_stale_cache_without_source_failure(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            first = self.make_crawler(cache_dir)
            first._request_resource_with_failover = Mock(side_effect=[
                (make_response(make_conference_index_xml()),
                 'https://primary.test/db/conf/ccs/index.xml'),
                (make_response(make_stream_xml()),
                 'https://primary.test/db/conf/ccs/ccs2026.xml'),
            ])
            first.search(['smart contract'], ['CCS'], 2024, 2026)
            first.close()

            for metadata_path in Path(cache_dir).glob('*.json'):
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                metadata['validated_at'] = (
                    datetime.now(timezone.utc) - timedelta(days=2)
                ).isoformat()
                metadata_path.write_text(json.dumps(metadata), encoding='utf-8')

            second = self.make_crawler(
                cache_dir, cache_ttl_hours=1, stale_cache_days=30
            )
            second._request_resource_with_failover = Mock(side_effect=[
                (None, 'all endpoints failed'),
                (None, 'all endpoints failed'),
            ])
            papers = second.search(['smart contract'], ['CCS'], 2024, 2026)

            self.assertEqual(1, len(papers))
            self.assertIsNone(second.source_failure_reason)
            self.assertEqual(2, second.cache_stats['stale'])
            second.close()

    def test_missing_stream_without_cache_marks_source_failure(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            crawler = self.make_crawler(cache_dir)
            crawler._request_resource_with_failover = Mock(return_value=(
                None, 'all endpoints failed'
            ))

            papers = crawler.search(['smart contract'], ['CCS'], 2024, 2026)

            self.assertEqual([], papers)
            self.assertIn('db/conf/ccs/index', crawler.source_failure_reason)
            self.assertEqual(1, crawler.cache_stats['missing'])
            crawler.close()

    def test_overlapping_venue_aliases_share_one_stream_download(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            crawler = self.make_crawler(cache_dir)
            crawler.prepare_domains([
                SimpleNamespace(keywords=['software'], ccf_venues=['FSE']),
                SimpleNamespace(keywords=['software'], ccf_venues=['ESEC/FSE']),
            ])
            sigsoft_index = make_conference_index_xml().replace(
                b'db/conf/ccs/', b'db/conf/sigsoft/'
            )
            crawler._request_resource_with_failover = Mock(side_effect=[
                (make_response(sigsoft_index),
                 'https://primary.test/db/conf/sigsoft/index.xml'),
                (make_response(make_stream_xml()),
                 'https://primary.test/db/conf/sigsoft/ccs2026.xml'),
            ])

            crawler.search(['smart contract'], ['FSE'], 2024, 2026)
            crawler.search(['agent memory'], ['ESEC/FSE'], 2024, 2026)

            self.assertEqual(2, crawler._request_resource_with_failover.call_count)
            crawler.close()

    def test_primary_failure_switches_to_official_mirror(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            bases = [
                'https://primary.test',
                'https://mirror.test',
            ]
            crawler = self.make_crawler(
                cache_dir, base_urls=bases
            )
            crawler._make_request = Mock(side_effect=[
                make_response(status=503),
                make_response(make_stream_xml()),
            ])

            response, url = crawler._request_resource_with_failover(
                'db/conf/ccs/index'
            )

            self.assertEqual(200, response.status_code)
            self.assertEqual(
                bases,
                [call.args[0].rsplit('/db/conf/ccs/index.xml', 1)[0]
                 for call in crawler._make_request.call_args_list],
            )
            self.assertEqual(bases[1], crawler._preferred_base_url)
            self.assertTrue(url.startswith(bases[1]))
            crawler.close()

    def test_unmapped_venue_is_reported_without_search_api_fallback(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            crawler = self.make_crawler(cache_dir)
            crawler._request_resource_with_failover = Mock()

            papers = crawler.search(['test'], ['UnknownConf'], 2024, 2026)

            self.assertEqual([], papers)
            self.assertIn('UnknownConf', crawler.source_failure_reason)
            crawler._request_resource_with_failover.assert_not_called()
            crawler.close()


if __name__ == '__main__':
    unittest.main()
