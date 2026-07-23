import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import scheduler


class SchedulerSafetyTests(unittest.TestCase):
    def tearDown(self):
        if scheduler._FETCH_THREAD_LOCK.locked():
            scheduler._release_fetch_lock()

    def test_fetch_lock_blocks_second_task_in_same_process(self):
        self.assertTrue(scheduler._acquire_fetch_lock())
        self.assertFalse(scheduler._acquire_fetch_lock())
        scheduler._release_fetch_lock()
        self.assertTrue(scheduler._acquire_fetch_lock())

    def test_catch_up_grace_uses_configured_minutes(self):
        scheduled = datetime(2026, 7, 21, 2, 0)
        with patch.object(scheduler.Config, 'CATCH_UP_GRACE_MINUTES', 30):
            deadline = scheduler._catch_up_grace_deadline(scheduled)

        self.assertEqual(scheduled + timedelta(minutes=30), deadline)

    def test_source_failure_marks_update_as_partial(self):
        self.assertEqual('success', scheduler._completion_status({}))
        self.assertEqual(
            'partial',
            scheduler._completion_status({'dblp': 'all endpoints failed'}),
        )

    def test_source_failure_uses_dblp_missing_cache_reason(self):
        crawler = SimpleNamespace(
            source_failure_reason='unavailable streams: db/conf/ccs/index',
            circuit_open=False,
            circuit_reason=None,
        )

        failures = scheduler._source_failure_details({'dblp': crawler})

        self.assertIn('unavailable streams', failures['dblp'])


if __name__ == '__main__':
    unittest.main()
