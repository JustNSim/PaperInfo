import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from zotero_service import ZoteroLocalClient, paper_to_zotero_item


def make_paper(**overrides):
    values = {
        'id': 7,
        'title': 'Example Paper',
        'authors': ['Alice Smith', 'Wang, Bob'],
        'abstract': 'Example abstract',
        'source': 'arxiv',
        'source_id': '2603.12345v2',
        'doi': None,
        'year': 2026,
        'venue': None,
        'url': 'https://arxiv.org/abs/2603.12345',
        'pdf_url': 'https://arxiv.org/pdf/2603.12345',
        'published_date': datetime(2026, 3, 20),
        'affiliation_names': ['Example University'],
        'domain': SimpleNamespace(name='Blockchain'),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ZoteroServiceTests(unittest.TestCase):
    def test_arxiv_mapping_uses_preprint_and_canonical_metadata(self):
        item = paper_to_zotero_item(make_paper())

        self.assertEqual('preprint', item['itemType'])
        self.assertEqual('10.48550/arXiv.2603.12345', item['DOI'])
        self.assertEqual('arXiv:2603.12345', item['archiveID'])
        self.assertEqual('Alice', item['creators'][0]['firstName'])
        self.assertEqual('Smith', item['creators'][0]['lastName'])
        self.assertIn('Affiliations: Example University', item['extra'])
        self.assertEqual([], item.get('attachments', []))

    def test_dblp_mapping_uses_conference_fields(self):
        item = paper_to_zotero_item(make_paper(
            source='dblp', source_id='conf/test/paper', doi='10.1234/test',
            venue='Example Conference',
        ))

        self.assertEqual('conferencePaper', item['itemType'])
        self.assertEqual('Example Conference', item['proceedingsTitle'])

    def test_local_client_pings_and_saves_in_batches(self):
        session = Mock()
        ping_response = Mock()
        ping_response.headers = {'X-Zotero-Version': '8.0'}
        ping_response.raise_for_status.return_value = None
        save_response = Mock(status_code=201, text='')
        session.get.return_value = ping_response
        session.post.return_value = save_response
        papers = [make_paper(id=index, pdf_url=None) for index in range(51)]
        client = ZoteroLocalClient(session=session)

        result = client.save_papers(papers, batch_size=50)

        self.assertEqual(51, result['saved'])
        self.assertEqual(2, result['batches'])
        self.assertEqual(51, result['pdf_skipped'])
        self.assertEqual('8.0', result['zotero_version'])
        self.assertEqual(2, session.post.call_count)
        first_payload = session.post.call_args_list[0].kwargs['json']
        self.assertEqual(50, len(first_payload['items']))
        self.assertTrue(first_payload['sessionID'].startswith('paperinfo-'))

    def test_pdf_is_downloaded_and_attached_to_parent_item(self):
        session = Mock()
        ping_response = Mock()
        ping_response.headers = {'X-Zotero-Version': '8.0'}
        ping_response.raise_for_status.return_value = None
        pdf_response = Mock()
        pdf_response.headers = {'Content-Length': '20'}
        pdf_response.content = b'%PDF-1.7 example data'
        pdf_response.raise_for_status.return_value = None
        session.get.side_effect = [ping_response, pdf_response]
        session.post.side_effect = [
            Mock(status_code=201, text=''),
            Mock(status_code=200, text=''),
        ]
        client = ZoteroLocalClient(session=session)

        result = client.save_papers([make_paper()])

        self.assertEqual(1, result['pdf_saved'])
        self.assertEqual(0, result['pdf_failed'])
        save_payload = session.post.call_args_list[0].kwargs['json']
        attachment_call = session.post.call_args_list[1]
        metadata = json.loads(attachment_call.kwargs['headers']['X-Metadata'])
        self.assertEqual(save_payload['sessionID'], metadata['sessionID'])
        self.assertEqual(save_payload['items'][0]['id'], metadata['parentItemID'])
        self.assertEqual(b'%PDF-1.7 example data', attachment_call.kwargs['data'])

    def test_selected_target_is_validated_and_session_is_moved(self):
        session = Mock()
        ping_response = Mock()
        ping_response.headers = {'X-Zotero-Version': '8.0'}
        ping_response.raise_for_status.return_value = None
        targets_response = Mock(status_code=200)
        targets_response.json.return_value = {
            'libraryID': 1,
            'libraryName': 'My Library',
            'id': 23,
            'name': 'AI Papers',
            'targets': [
                {'id': 'L1', 'name': 'My Library', 'level': 0},
                {'id': 'C23', 'name': 'AI Papers', 'level': 1},
            ],
        }
        save_response = Mock(status_code=201, text='')
        update_response = Mock(status_code=200, text='')
        session.get.return_value = ping_response
        session.post.side_effect = [targets_response, save_response, update_response]
        client = ZoteroLocalClient(session=session)

        result = client.save_papers(
            [make_paper(pdf_url=None)], target_id='C23'
        )

        self.assertEqual('AI Papers', result['target_name'])
        update_call = session.post.call_args_list[2]
        self.assertTrue(update_call.args[0].endswith('/connector/updateSession'))
        self.assertEqual('C23', update_call.kwargs['json']['target'])
        self.assertEqual(
            session.post.call_args_list[1].kwargs['json']['sessionID'],
            update_call.kwargs['json']['sessionID'],
        )


if __name__ == '__main__':
    unittest.main()
