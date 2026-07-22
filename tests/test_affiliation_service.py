import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import inspect, text

from affiliation_service import OpenAlexAffiliationClient, _parse_openalex_work
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
            'affiliations_fetched_at', 'doi',
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
