import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
from sqlalchemy import inspect, text

from affiliation_service import (
    OpenAlexAffiliationClient,
    ArxivMetadataClient,
    CrossrefMetadataClient,
    PDFURLValidator,
    PublisherMetadataClient,
    UnpaywallClient,
    _parse_openalex_abstract,
    _parse_openalex_pdf_url,
    _parse_openalex_work,
    _parse_unpaywall_pdf_url,
    _schedule_next_metadata_retry,
)
from crawler.arxiv import ArxivCrawler
from crawler.dblp import DBLPCrawler
from models import Paper, db, ensure_paper_schema


OPENALEX_WORK = {
    'title': 'Example Paper',
    'authorships': [
        {
            'author': {'display_name': 'Alice'},
            'institutions': [
                {'display_name': 'Example University'},
                {'display_name': 'Example University'},
            ],
        },
        {
            'author': {'display_name': 'Bob'},
            'institutions': [],
            'raw_affiliation_strings': ['Example Research Lab'],
        },
    ],
}


class AffiliationServiceTests(unittest.TestCase):
    def test_legacy_database_gets_affiliation_columns_without_rebuild(self):
        app = Flask(__name__)
        app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite://',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)
        with app.app_context():
            with db.engine.begin() as connection:
                connection.execute(text('CREATE TABLE papers (id INTEGER PRIMARY KEY)'))
            ensure_paper_schema()
            columns = {column['name'] for column in inspect(db.engine).get_columns('papers')}

        self.assertTrue({
            'author_affiliations', 'affiliations_source', 'affiliations_status',
            'affiliations_fetched_at', 'doi', 'abstract_source', 'pdf_source',
            'metadata_fetched_at', 'metadata_retry_count',
            'metadata_next_retry_at',
        }.issubset(columns))

    def test_affiliation_names_are_unique_and_keep_order(self):
        paper = Paper(author_affiliations=[
            {'name': 'Alice', 'affiliations': ['Example University', 'Research Lab']},
            {'name': 'Bob', 'affiliations': ['example university']},
        ])
        self.assertEqual(['Example University', 'Research Lab'], paper.affiliation_names)

    def test_openalex_work_preserves_author_affiliation_mapping(self):
        self.assertEqual(
            [
                {'name': 'Alice', 'affiliations': ['Example University']},
                {'name': 'Bob', 'affiliations': ['Example Research Lab']},
            ],
            _parse_openalex_work(OPENALEX_WORK),
        )

    def test_openalex_abstract_inverted_index_is_reconstructed(self):
        work = {
            'abstract_inverted_index': {
                'methods.': [3], 'We': [0], 'new': [2], 'present': [1],
            }
        }
        self.assertEqual('We present new methods.', _parse_openalex_abstract(work))

    def test_openalex_pdf_prefers_best_oa_location(self):
        work = {
            'best_oa_location': {'pdf_url': 'https://oa.test/paper.pdf'},
            'primary_location': {'pdf_url': 'https://publisher.test/paper.pdf'},
            'locations': [{'pdf_url': 'https://repository.test/paper.pdf'}],
        }
        self.assertEqual(
            'https://oa.test/paper.pdf', _parse_openalex_pdf_url(work)
        )

    def test_unpaywall_pdf_prefers_best_oa_location(self):
        record = {
            'best_oa_location': {
                'url_for_pdf': 'https://repository.test/best.pdf'
            },
            'oa_locations': [
                {'url_for_pdf': 'https://repository.test/other.pdf'}
            ],
        }
        self.assertEqual(
            'https://repository.test/best.pdf',
            _parse_unpaywall_pdf_url(record),
        )

    def test_unpaywall_client_uses_configured_contact_email(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            'best_oa_location': {
                'url_for_pdf': 'https://repository.test/paper.pdf'
            }
        }
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        client = UnpaywallClient(email='researcher@example.test', session=session)

        pdf_url = client.fetch_pdf_url('10.1109/example')

        self.assertEqual('https://repository.test/paper.pdf', pdf_url)
        request = session.get.call_args
        self.assertEqual(
            {'email': 'researcher@example.test'}, request.kwargs['params']
        )
        self.assertIn('10.1109%2Fexample', request.args[0])

    def test_crossref_extracts_jats_abstract_and_pdf_candidates(self):
        response = Mock(status_code=200)
        response.json.return_value = {'message': {
            'abstract': '<jats:p>A useful <jats:bold>abstract</jats:bold>.</jats:p>',
            'link': [
                {'content-type': 'application/pdf', 'URL': 'https://test/paper.pdf'},
                {'content-type': 'text/xml', 'URL': 'https://test/paper.xml'},
            ],
        }}
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        client = CrossrefMetadataClient(session=session)

        metadata = client.fetch_metadata('10.1109/example')

        self.assertEqual('A useful abstract.', metadata['abstract'])
        self.assertEqual(['https://test/paper.pdf'], metadata['pdf_urls'])
        self.assertIn('10.1109%2Fexample', session.get.call_args.args[0])

    def test_arxiv_requires_exact_title_and_author_overlap(self):
        atom = b'''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry><id>https://arxiv.org/abs/2501.00001v2</id>
            <title>Exact Paper Title</title><summary>Useful abstract.</summary>
            <published>2025-01-01T00:00:00Z</published>
            <author><name>Alice Smith</name></author>
            <link title="pdf" href="https://arxiv.org/pdf/2501.00001v2" type="application/pdf"/>
          </entry>
        </feed>'''
        response = Mock(content=atom)
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        client = ArxivMetadataClient(session=session)
        paper = SimpleNamespace(
            title='Exact Paper Title', authors=['Alice Smith'], year=2026,
        )

        metadata = client.fetch_metadata(paper)

        self.assertEqual('Useful abstract.', metadata['abstract'])
        self.assertEqual(
            ['https://arxiv.org/pdf/2501.00001v2'], metadata['pdf_urls']
        )

        paper.authors = ['Different Author']
        self.assertEqual(
            {'abstract': None, 'pdf_urls': []}, client.fetch_metadata(paper)
        )

    def test_publisher_metadata_requires_matching_citation_title(self):
        response = Mock(
            status_code=200,
            content=b'html',
            text='''<meta name="citation_title" content="Exact Paper">
                    <meta name="citation_abstract" content="A sufficiently long publisher abstract for testing.">
                    <meta name="citation_pdf_url" content="/paper.pdf">''',
            url='https://publisher.test/article',
        )
        response.headers = {'Content-Type': 'text/html; charset=utf-8'}
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        client = PublisherMetadataClient(session=session)
        paper = SimpleNamespace(
            url='https://doi.org/10.test/example', title='Exact Paper',
        )

        metadata = client.fetch_metadata(paper)

        self.assertIn('publisher abstract', metadata['abstract'])
        self.assertEqual(
            ['https://publisher.test/paper.pdf'], metadata['pdf_urls']
        )

    def test_pdf_validator_rejects_login_html(self):
        html_response = Mock(status_code=200)
        html_response.iter_content.return_value = [b'<html>login</html>']
        pdf_response = Mock(status_code=206)
        pdf_response.iter_content.return_value = [b'%PDF-1.7 content']
        session = Mock()
        session.get.side_effect = [html_response, pdf_response]
        validator = PDFURLValidator(session=session)

        self.assertFalse(validator.is_public_pdf('https://test/login'))
        self.assertTrue(validator.is_public_pdf('https://test/paper.pdf'))

    def test_metadata_retry_uses_progressive_intervals(self):
        paper = SimpleNamespace(
            metadata_retry_count=0, abstract=None, pdf_url=None,
            metadata_next_retry_at=None,
        )
        now = datetime(2026, 7, 23, 12, 0)
        with patch('affiliation_service.Config.METADATA_RETRY_HOURS', (24, 72, 168, 720)):
            _schedule_next_metadata_retry(paper, now)
            self.assertEqual(now + timedelta(hours=24), paper.metadata_next_retry_at)
            _schedule_next_metadata_retry(paper, paper.metadata_next_retry_at)
            self.assertEqual(now + timedelta(hours=72), paper.metadata_next_retry_at)

    def test_arxiv_paper_uses_canonical_doi_before_title_search(self):
        client = OpenAlexAffiliationClient(api_key='test-key')
        paper = SimpleNamespace(
            doi=None,
            source='arxiv',
            source_id='2401.12345v3',
            title='Example Paper',
            year=2026,
        )
        with patch.object(client, '_lookup_by_doi', return_value=OPENALEX_WORK) as doi_lookup, \
                patch.object(client, '_lookup_by_title') as title_lookup:
            details = client.fetch_for_paper(paper)

        doi_lookup.assert_called_once_with('10.48550/arXiv.2401.12345')
        title_lookup.assert_not_called()
        self.assertEqual('Example University', details[0]['affiliations'][0])
        client.close()

    def test_lookup_distinguishes_missing_work_from_missing_affiliations(self):
        client = OpenAlexAffiliationClient(api_key='test-key')
        paper = SimpleNamespace(
            doi='10.1234/example', source='dblp', source_id='example',
            title='Example Paper', year=2026,
        )
        work_without_affiliations = {
            'title': 'Example Paper',
            'authorships': [{
                'author': {'display_name': 'Alice'},
                'institutions': [],
                'raw_affiliation_strings': [],
            }],
        }

        with patch.object(client, '_lookup_by_doi',
                          return_value=work_without_affiliations):
            outcome, details = client.fetch_result_for_paper(paper)
        self.assertEqual('affiliation_missing', outcome)
        self.assertEqual([], details)

        with patch.object(client, '_lookup_by_doi', return_value=None), \
                patch.object(client, '_lookup_by_title', return_value=None):
            outcome, details = client.fetch_result_for_paper(paper)
        self.assertEqual('paper_not_found', outcome)
        self.assertEqual([], details)
        client.close()

    def test_metadata_lookup_reuses_work_for_abstract_and_pdf(self):
        client = OpenAlexAffiliationClient(api_key='test-key')
        paper = SimpleNamespace(
            doi='10.1234/example', source='dblp', source_id='example',
            title='Example Paper', year=2026,
        )
        work = {
            **OPENALEX_WORK,
            'abstract_inverted_index': {'Useful': [0], 'abstract.': [1]},
            'best_oa_location': {'pdf_url': 'https://oa.test/example.pdf'},
        }
        with patch.object(client, '_lookup_by_doi', return_value=work):
            outcome, metadata = client.fetch_metadata_for_paper(paper)

        self.assertEqual('success', outcome)
        self.assertEqual('Useful abstract.', metadata['abstract'])
        self.assertEqual('https://oa.test/example.pdf', metadata['pdf_url'])
        self.assertEqual('Example University', metadata['author_affiliations'][0]['affiliations'][0])
        client.close()

    def test_arxiv_parser_keeps_optional_affiliation(self):
        crawler = ArxivCrawler(delay=0, max_retries=0)
        entry = {
            'title': 'Example',
            'authors': [
                {'name': 'Alice', 'arxiv_affiliation': 'Example University'},
                {'name': 'Bob'},
            ],
            'summary': 'Abstract',
            'published': '2026-07-22T01:02:03Z',
            'id': 'https://arxiv.org/abs/2401.12345v2',
            'links': [],
        }
        paper = crawler._parse_entry(entry)
        crawler.close()

        self.assertEqual(['Alice', 'Bob'], paper['authors'])
        self.assertEqual(
            [{'name': 'Alice', 'affiliations': ['Example University']}],
            paper['author_affiliations'],
        )
        self.assertEqual('10.48550/arXiv.2401.12345', paper['doi'])

    def test_dblp_parser_extracts_doi_from_electronic_edition(self):
        crawler = DBLPCrawler(delay=0, max_retries=0)
        paper = crawler._parse_entry({
            'info': {
                'title': 'Example',
                'authors': {'author': {'text': 'Alice'}},
                'year': '2026',
                'url': 'https://dblp.org/rec/conf/test/example',
                'ee': 'https://doi.org/10.1234/example.2026',
            }
        })
        crawler.close()

        self.assertEqual('10.1234/example.2026', paper['doi'])


if __name__ == '__main__':
    unittest.main()
