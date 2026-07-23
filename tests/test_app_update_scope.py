import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app import _update_log_paper_scope


class UpdateLogPaperScopeTests(unittest.TestCase):
    def test_uses_real_started_at_and_processed_domains(self):
        completed = datetime(2026, 7, 22, 20, 20, 19)
        log = SimpleNamespace(
            trigger_time=completed,
            operation_details={'started_at': '2026-07-22T20:11:31.338185'},
            domains_processed=[1, 2, 3],
        )

        started, ended, domains = _update_log_paper_scope(log)

        self.assertEqual(datetime(2026, 7, 22, 20, 11, 31, 338185), started)
        self.assertEqual(completed, ended)
        self.assertEqual([1, 2, 3], domains)

    def test_old_log_uses_compatible_five_minute_window(self):
        completed = datetime(2026, 7, 22, 20, 20)
        log = SimpleNamespace(
            trigger_time=completed,
            operation_details={},
            domains_processed=[],
        )

        started, ended, domains = _update_log_paper_scope(log)

        self.assertEqual(completed - timedelta(minutes=5), started)
        self.assertEqual(completed + timedelta(minutes=5), ended)
        self.assertEqual([], domains)


if __name__ == '__main__':
    unittest.main()
