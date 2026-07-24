import re
import unittest

from app import create_app


class AppSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing', start_scheduler=False)
        self.client = self.app.test_client()

    def test_mutation_without_csrf_token_is_rejected(self):
        response = self.client.post('/api/translate', json={'text': 'hello'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['message'], 'CSRF 校验失败，请刷新页面后重试')

    def test_page_token_allows_same_session_mutation(self):
        page = self.client.get('/')
        token_match = re.search(
            rb'<meta name="csrf-token" content="([^"]+)">',
            page.data,
        )
        self.assertIsNotNone(token_match)

        response = self.client.post(
            '/api/translate',
            json={'text': ''},
            headers={'X-CSRF-Token': token_match.group(1).decode()},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['message'], '待翻译文本不能为空')

    def test_csrf_token_endpoint_establishes_session(self):
        token_response = self.client.get('/api/csrf-token')
        token = token_response.get_json()['csrf_token']

        response = self.client.post(
            '/api/translate',
            json={'text': ''},
            headers={'X-CSRF-Token': token},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['message'], '待翻译文本不能为空')


if __name__ == '__main__':
    unittest.main()
