import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from notification_service import build_update_message, send_update_notification


def make_update_log(**overrides):
    values = {
        'id': 42,
        'trigger_type': 'scheduled',
        'trigger_time': datetime(2026, 7, 21, 2, 3, 4),
        'status': 'success',
        'total_new': 7,
        'source_stats': {'arxiv': 5, 'dblp': 2},
        'llm_filtered': 3,
        'operation_details': {'duration_seconds': 12.5, 'source_failures': {}},
        'error_message': None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class NotificationServiceTests(unittest.TestCase):
    def test_build_update_message_contains_result_summary(self):
        with patch('notification_service.Config.PAPERINFO_PUBLIC_URL', 'http://paperinfo.local:5000'):
            message = build_update_message(make_update_log(), ['区块链', '软件工程'])

        self.assertIn('PaperInfo 定时更新成功', message)
        self.assertIn('新增：7 篇', message)
        self.assertIn('arXiv 5 篇、DBLP 2 篇', message)
        self.assertIn('领域：区块链、软件工程', message)
        self.assertIn('http://paperinfo.local:5000/?update_log=42', message)

    @patch('notification_service.requests.post')
    def test_send_notification_posts_signed_text_payload(self, post):
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
        self.assertEqual('text', payload['msg_type'])
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
