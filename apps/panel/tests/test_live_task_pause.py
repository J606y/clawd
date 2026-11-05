"""Real cross-process gate tests; all state is isolated from production."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch, Mock

from sidecar import hermes_panel_sidecar as sc
from native_browser_fixture import bind_profile, install_native, register_page

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('pause_guard', ROOT / 'plugins/browser-window-guard/__init__.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.root = Path(self.tmp.name)
        bind_profile(self, self.root / 'profile')
        install_native(sc, self)
        register_page(task_id='t', session_id='s', turn_id='turn-s', target_id='OWN',
                      browser_id='/devtools/browser/test', endpoint='http://127.0.0.1:9')
        self.transport = patch('tools.browser_task_cdp.page_ws', return_value=('ws://127.0.0.1:9/devtools/page/OWN', 'owned'))
        self.transport.start()
        self.addCleanup(self.transport.stop)
        self.body = dict(action='takeover', clientId=str(uuid.uuid4()), sessionId='s', taskId='t', targetId='OWN')
        self.children = []

    def tearDown(self):
        try:
            sc.live_task_control(dict(action='release', clientId=self.body['clientId']))
            for child in self.children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)
        finally:
            self.tmp.cleanup()

    def ready(self):
        from tools.browser_task_registry import session_view
        current = session_view('s', 't')
        self.assertEqual(current['status'], 'active')
        self.assertEqual(current['targetId'], 'OWN')

    def gate(self):
        from tools.browser_task_registry import gate_path
        return gate_path()

    def test_active_run_pauses_at_real_lock_and_release_resumes_worker(self):
        self.ready()
        lock = self.gate()
        with lock.open('w') as ai:
            fcntl.flock(ai, fcntl.LOCK_EX)
            pending = sc.live_task_control(self.body)
            self.assertFalse(pending['controlledByYou'])
            self.assertTrue(pending['pending'])
        result = sc.live_task_control(self.body)
        self.assertTrue(result['controlledByYou'])
        marker = self.root / 'resumed'
        code = "import fcntl,pathlib,sys; f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_EX); pathlib.Path(sys.argv[2]).write_text('AI resumed')"
        child = subprocess.Popen([sys.executable, '-c', code, str(lock), str(marker)])
        self.children.append(child)
        time.sleep(.15)
        self.assertFalse(marker.exists(), 'AI must not operate during takeover')
        sc.live_task_control(dict(action='release', clientId=self.body['clientId']))
        child.wait(timeout=4)
        self.assertEqual(marker.read_text(), 'AI resumed')

    def test_expiration_releases_lock_without_another_http_request(self):
        self.ready()
        with patch.object(sc, 'LIVE_LEASE_S', .12):
            self.assertTrue(sc.live_task_control(self.body)['controlledByYou'])
        time.sleep(.3)
        with self.gate().open('w') as probe:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_old_plugin_is_rejected_even_if_session_is_idle(self):
        from sidecar.live_task_store import ShadowStoreRetired, Store
        with self.assertRaises(ShadowStoreRetired):
            Store(self.root / 'tasks.sqlite3')
        idle = dict(self.body, taskId='old-plugin-task', targetId='UNREGISTERED')
        with self.assertRaises(sc.ApiError) as error:
            sc.live_task_control(idle)
        self.assertIn(error.exception.code, {'live_task_ended', 'live_target_unowned', 'live_task_mismatch'})
        self.assertNotEqual(error.exception.status, 200)

    def test_same_client_cannot_bypass_task_gate_through_global_control(self):
        self.ready()
        self.assertTrue(sc.live_task_control(self.body)['controlledByYou'])
        with patch.object(sc, '_live_page', return_value={'title': 'page'}), patch.object(sc, 'get_session_run', return_value={'busy': False, 'run': None}):
            with self.assertRaises(sc.ApiError):
                sc.live_takeover({'clientId': self.body['clientId'], 'sessionId': ''})

    def test_canceled_timeout_cannot_release_renewed_gate(self):
        self.ready()
        sc.live_task_control(self.body)
        with sc._LIVE_LOCK:
            gate, old_deadline = sc._live_task_lease['gate'], sc._live_task_lease['until']
        sc.live_task_control(self.body)
        sc._live_task_timeout(gate, old_deadline)
        self.assertFalse(gate.closed)

    def test_guard_budget_includes_bounded_manual_pause(self):
        from clawd_server.browser_native import _HARD_LEASE_S
        hooks = {}
        ctx = Mock()
        ctx.register_hook.side_effect = lambda name, fn: hooks.update({name: fn})
        guard.register(ctx)
        result = hooks['pre_tool_call'](session_id='s', turn_id='t', tool_name='browser_exec', args={'code': '# check\nprint(1)', 'timeout_s': 30})
        self.assertIsNone(result)
        self.assertGreaterEqual(_HARD_LEASE_S, 300)
        self.assertFalse('on_session_end' in hooks)

    def test_hard_cap_cannot_be_silently_reacquired_by_heartbeat(self):
        self.ready()
        sc.live_task_control(self.body)
        with sc._LIVE_LOCK:
            sc._live_task_lease['hardUntil'] = time.monotonic() - 1
            sc._live_task_drop()
        with self.assertRaises(sc.ApiError) as error:
            sc.live_task_control(self.body)
        self.assertEqual(error.exception.code, 'live_control_limit')

    def test_finalizer_waits_for_real_lock_without_blocking_hook(self):
        from tools.browser_task_registry import close_owned, session_view
        self.ready()
        register_page(task_id='other-task', session_id='s', turn_id='finalizer-turn', target_id='OWN2',
                      browser_id='/devtools/browser/test')
        with self.gate().open('w') as user:
            fcntl.flock(user, fcntl.LOCK_EX)
            started = time.monotonic()
            pending = sc.live_task_control(self.body)
            self.assertFalse(pending['controlledByYou'])
            self.assertTrue(pending['pending'])
            close_owned('other-task')
            self.assertLess(time.monotonic() - started, .5, 'cleanup must not wait behind the user lease')
            self.assertEqual(session_view('s', 't')['status'], 'active')
            self.assertEqual(session_view('s', 'other-task')['status'], 'ended')


if __name__ == '__main__':
    unittest.main()
