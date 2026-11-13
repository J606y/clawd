"""Read-only retry evidence using isolated synthetic logs; never touch agent logs."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from clawd_server import panel_core as s

class RunWaitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = time.time()
        self.started = int((self.now - 5) * 1000)
        self.sid = 'spinner-fixture'
        self.log = self.root / 'agent.log'

    def line(self, text, age=1, sid=None):
        ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.now-age))
        return f'{ts},000 WARNING [{sid or self.sid}] agent.conversation_loop: {text}\n'

    def put(self, extra='', age=1, summary='HTTP 429: too many requests'):
        self.log.write_text(self.line(f'API call failed (attempt 2/3) error_type=RateLimitError model=fixture summary={summary}',age)+self.line('Retrying API call in 60.0s (attempt 2/3) model=fixture',age)+extra)

    def query(self, since=None):
        with patch.object(s,'LOG_DIR',self.root):
            import inspect
            if 'turn_started_at_ms' not in inspect.signature(s.get_run_wait).parameters:
                return s.get_run_wait(self.sid)
            return s.get_run_wait(self.sid,self.started if since is None else since)

    def test_current_turn_contains_evidenced_status_and_absolute_deadline(self):
        self.put()
        w=self.query()
        self.assertEqual(w['state'],'retrying')
        self.assertEqual(w['httpStatus'],429)
        self.assertEqual(w['turnStartedAtMs'],self.started)
        self.assertEqual(w['source'],'log')
        self.assertGreater(w['nextRetryAtMs'],w['observedAtMs'])

    def test_previous_turn_is_not_current_retry(self):
        self.put(age=20)
        self.assertEqual(self.query()['state'],'idle')

    def test_recovery_terminal_and_giveup_clear_retry(self):
        for msg in ['API call #3: model=fixture','Turn ended: done','API call failed after 3 retries. HTTP 429']:
            self.put(extra=self.line(msg,age=0))
            self.assertEqual(self.query()['state'],'idle')

    def test_unknown_status_is_not_invented(self):
        self.put(summary='RateLimitError')
        self.assertNotIn('httpStatus',self.query())

    def test_missing_logs_are_explicitly_unavailable(self):
        self.assertFalse(self.query()['available'])

    def test_bad_boundaries_are_rejected(self):
        for value in ['nan','inf','invalid',-1,10**18]:
            with self.assertRaises(s.ApiError): self.query(value)

    def test_rotation_and_matching_attempt_boundary(self):
        self.put()
        self.log.rename(self.root/'agent.log.1')
        self.log.write_text(self.line('unrelated activity',sid='another-session'))
        self.assertEqual(self.query()['httpStatus'],429)
        self.log.write_text(self.line('API call #3: model=fixture',age=0))
        self.assertEqual(self.query()['state'],'idle')

    def test_http_status_is_paired_with_same_attempt(self):
        self.log.write_text(self.line('API call failed (attempt 1/3) error_type=Error model=fixture summary=HTTP 503')+self.line('Retrying API call in 60s (attempt 2/3) model=fixture'))
        self.assertNotIn('httpStatus',self.query())

if __name__=='__main__': unittest.main()
