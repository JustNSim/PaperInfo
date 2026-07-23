import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from models import Domain, Paper, db
from notification_service import (
    _abstract_summary,
    _recent_new_papers,
    build_update_card,
    send_update_notification,
)


def make_update_log(**overrides):
    values = {
        'id': 42,
        'trigger_type': 'scheduled',
        'trigger_time': datetime(2026, 7, 21, 2, 3, 4),
        'status': 'success',
        'total_new': 7,
        'source_stats': {'arxiv': 5, 'dblp': 2},
        'llm_filtered': 3,
        'operation_details': {
            'started_at': datetime(2026, 7, 21, 2, 0, 0).isoformat(),
            'duration_seconds': 12.5,
            'source_failures': {},
        },
        'domains_processed': [1],
        'error_message': None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class NotificationServiceTests(unittest.TestCase):
    @patch('notification_service._recent_new_papers')
    def test_build_card_contains_at_most_five_linked_papers(self, recent_papers):
        recent_papers.return_value = [
            SimpleNamespace(
                title=f'Paper {index}',
                url=f'https://example.test/paper/{index}',
                source='arxiv',
                llm_score=90 - index,
                llm_value_score=80 - index,
                published_date=datetime(2026, 7, index),
                abstract='A' * 120,
            )
            for index in range(1, 7)
        ]
        card = build_update_card(make_update_log(), ['区块链'])
        contents = '\n'.join(
            element['text']['content']
            for element in card['elements']
            if element.get('tag') == 'div'
        )

        self.assertEqual('PaperInfo 更新成功', card['header']['title']['content'])
        self.assertIn('新增 7 篇｜arXiv 5｜DBLP 2｜耗时 12.5 秒', contents)
        self.assertIn('[Paper 1](https://example.test/paper/1)', contents)
        self.assertIn('arXiv｜相关 89｜价值 79｜07-01', contents)
        self.assertIn('5. [Paper 5]', contents)
        self.assertNotIn('Paper 6', contents)
        self.assertIn('另有 2 篇', contents)
        recent_papers.assert_called_once_with(make_update_log(), limit=5)

    def test_abstract_summary_is_single_line_and_at_most_100_chars(self):
        summary = _abstract_summary(('word\n' * 40) + 'tail')
        self.assertNotIn('\n', summary)
        self.assertLessEqual(len(summary), 100)
        self.assertTrue(summary.endswith('…'))

    @patch('notification_service._recent_new_papers', return_value=[])
    def test_partial_update_card_reports_source_failure(self, _recent_papers):
        update_log = make_update_log(
            status='partial',
            operation_details={
                'started_at': datetime(2026, 7, 21, 2, 0, 0).isoformat(),
                'duration_seconds': 30,
                'source_failures': {
                    'dblp': 'all DBLP endpoints failed',
                },
            },
        )

        card = build_update_card(update_log)
        contents = '\n'.join(
            element['text']['content']
            for element in card['elements']
            if element.get('tag') == 'div'
        )

        self.assertEqual('PaperInfo 部分成功', card['header']['title']['content'])
        self.assertEqual('orange', card['header']['template'])
        self.assertIn('来源异常', contents)
        self.assertIn('dblp', contents)

    def test_recent_papers_use_started_at_end_time_and_domain(self):
        app = Flask(__name__)
        app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite://',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)
        started_at = datetime(2026, 7, 21, 2, 0, 0)
        ended_at = datetime(2026, 7, 21, 2, 30, 0)

        with app.app_context():
            db.create_all()
            domain = Domain(name='目标领域', keywords=[])
            other_domain = Domain(name='其他领域', keywords=[])
            db.session.add_all([domain, other_domain])
            db.session.commit()
            db.session.add_all([
                Paper(title='本批次论文', authors=[], source='arxiv', domain_id=domain.id,
                      fetched_date=started_at + timedelta(minutes=10), llm_score=90),
                Paper(title='开始前论文', authors=[], source='arxiv', domain_id=domain.id,
                      fetched_date=started_at - timedelta(seconds=1), llm_score=99),
                Paper(title='其他领域论文', authors=[], source='arxiv', domain_id=other_domain.id,
                      fetched_date=started_at + timedelta(minutes=10), llm_score=100),
            ])
            db.session.commit()

            update_log = make_update_log(
                trigger_time=ended_at,
                total_new=1,
                domains_processed=[domain.id],
                operation_details={
                    'started_at': started_at.isoformat(),
                    'duration_seconds': 1800,
                    'source_failures': {},
                },
            )
            papers = _recent_new_papers(update_log)
            self.assertEqual(['本批次论文'], [paper.title for paper in papers])
            db.session.remove()
            db.drop_all()

    @patch('notification_service.requests.post')
    @patch('notification_service._recent_new_papers', return_value=[])
    def test_send_notification_posts_signed_card_payload(self, _recent_papers, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'code': 0}
        post.return_value = response

        with patch('notification_service.Config.FEISHU_WEBHOOK_URL', 'https://example.test/hook'), \
                patch('notification_service.Config.FEISHU_WEBHOOK_SECRET', 'secret'), \
                patch('notification_service.Config.FEISHU_NOTIFICATION_TIMEOUT', 6), \
                patch('notification_service.time.time', return_value=1234567890):
            sent = send_update_notification(make_update_log(), ['区块链'])

        self.assertTrue(sent)
        payload = post.call_args.kwargs['json']
        self.assertEqual('interactive', payload['msg_type'])
        self.assertIn('card', payload)
        self.assertNotIn('content', payload)
        self.assertEqual('1234567890', payload['timestamp'])
        self.assertTrue(payload['sign'])
        self.assertEqual(6, post.call_args.kwargs['timeout'])

    @patch('notification_service.requests.post')
    def test_notification_failure_is_non_fatal(self, post):
        post.side_effect = RuntimeError('network down')
        with patch('notification_service.Config.FEISHU_WEBHOOK_URL', 'https://example.test/hook'), \
                patch('notification_service.Config.FEISHU_WEBHOOK_SECRET', None):
            self.assertFalse(send_update_notification(make_update_log()))

    @patch('notification_service.requests.post')
    def test_no_webhook_disables_notification(self, post):
        with patch('notification_service.Config.FEISHU_WEBHOOK_URL', None):
            self.assertFalse(send_update_notification(make_update_log()))
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
