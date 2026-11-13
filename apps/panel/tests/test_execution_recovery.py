"""Synthetic databases and isolated CLI workers; never touches real sessions/services."""
import sys as _sys
from pathlib import Path as _Path
_REPO_ROOT = _Path(__file__).resolve().parents[3]
_TESTS_DIR = _Path(__file__).resolve().parent
if str(_TESTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.append(str(_REPO_ROOT))

from clawd_server import panel_core
from panel_core_http_fixture import start_panel_core_server, stop_panel_core_server

import contextlib
import http.client
import threading
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


class ExecutionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.m = panel_core
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.patch = patch.object(self.m, 'HERMES_HOME', self.home)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.sid = 'synthetic_cli_session'
        self.ns = os.stat('/proc/self/ns/pid').st_ino
        with self.db() as c:
            c.executescript('CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL); CREATE TABLE session_turn_leases (conversation_id TEXT PRIMARY KEY, holder TEXT, acquired_at REAL, expires_at REAL);')
            c.execute('INSERT INTO sessions (id, source) VALUES (?, ?)', (self.sid, 'cli'))

    @contextlib.contextmanager
    def db(self):
        c = sqlite3.connect(self.home / 'state.db')
        try:
            with c:
                yield c
        finally:
            c.close()

    def lease(self, pid, *, acquired=None, platform='cli', ns=None, sid=None):
        sid = sid or self.sid
        at = time.time() if acquired is None else acquired
        holder = f'pid={pid}:pidns={self.ns if ns is None else ns}:turn={sid}:{sid}:fixture:platform={platform}'
        with self.db() as c:
            c.execute('INSERT OR REPLACE INTO session_turn_leases VALUES (?, ?, ?, ?)', (sid, holder, at, time.time() + 300))
        return holder

    def worker(self, stubborn=False):
        code = 'import time, signal; marker="hermes_cli.main"; '
        if stubborn:
            code += 'signal.signal(signal.SIGINT, signal.SIG_IGN); signal.signal(signal.SIGTERM, signal.SIG_IGN); '
        code += 'print("ready", flush=True); time.sleep(60)'
        p = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.kill(), p.wait()) if p.poll() is None else None)
        assert p.stdout is not None
        self.addCleanup(p.stdout.close)
        self.assertEqual(p.stdout.readline().strip(), 'ready')
        # Test workers inherit the test runner's gateway cgroup. Model only the isolated
        # CLI cgroup boundary; process identity, pidfd signals and database writes stay real.
        original_read = Path.read_text
        target = f'/proc/{p.pid}/cgroup'
        def fixture_read(path, *args, **kwargs):
            return '0::/fixture-cli' if str(path) == target else original_read(path, *args, **kwargs)
        cgroups = patch.object(Path, 'read_text', fixture_read)
        cgroups.start()
        self.addCleanup(cgroups.stop)
        return p

    def test_dead_cli_lease_is_not_reported_as_busy(self):
        self.lease(2147483647)
        self.assertEqual(self.m.get_session_run(self.sid), {'run': None, 'busy': False})


    def test_verified_cli_stop_ends_real_worker_and_clears_exact_lease(self):
        p = self.worker(stubborn=True)
        self.lease(p.pid)
        state = self.m.get_session_execution(self.sid)
        self.assertTrue(state['busy'])
        self.assertNotIn(str(p.pid), json.dumps(state))
        descriptor = state['execution']
        self.assertIsNotNone(descriptor)
        result = self.m.stop_session_execution({'session_id': self.sid, 'execution_id': descriptor['id'], 'confirm': 'stop-cli'})
        self.assertEqual(result, {'status': 'cancelled', 'busy': False})
        p.wait(timeout=3)
        with self.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM session_turn_leases').fetchone()[0], 0)

    def test_replacement_during_cleanup_is_preserved_and_not_reported_idle(self):
        old = self.worker()
        replacement = self.worker()
        self.lease(old.pid)
        descriptor = self.m.get_session_execution(self.sid)['execution']
        snapshot = self.m._session_execution_snapshot
        calls = 0
        def replace_after_exit(sid):
            nonlocal calls
            calls += 1
            result = snapshot(sid)
            if calls == 3:
                self.assertIsNone(result)
                self.lease(replacement.pid)
            return result
        with patch.object(self.m, '_session_execution_snapshot', replace_after_exit):
            with self.assertRaises(self.m.ApiError) as caught:
                self.m.stop_session_execution({'session_id': self.sid, 'execution_id': descriptor['id'], 'confirm': 'stop-cli'})
        self.assertEqual(caught.exception.code, 'execution_changed')
        self.assertIsNone(replacement.poll())
        with self.db() as c:
            self.assertIn(f'pid={replacement.pid}:', c.execute('SELECT holder FROM session_turn_leases').fetchone()[0])

    def test_wrong_execution_id_never_signals_worker(self):
        p = self.worker()
        self.lease(p.pid)
        with self.assertRaises(self.m.ApiError) as caught:
            self.m.stop_session_execution({'session_id': self.sid, 'execution_id': '0' * 64, 'confirm': 'stop-cli'})
        self.assertEqual(caught.exception.status, 409)
        self.assertIsNone(p.poll())


    def test_guards_refuse_api_namespace_shared_and_reused_pid(self):
        p = self.worker()
        for case in ('api', 'namespace', 'shared', 'reused', 'gateway-cgroup'):
            with self.subTest(case=case):
                with self.db() as c:
                    c.execute('DELETE FROM session_turn_leases')
                    c.execute('UPDATE sessions SET source = ? WHERE id = ?', ('api_server' if case == 'api' else 'cli', self.sid))
                self.lease(p.pid, ns=self.ns + 1 if case == 'namespace' else None,
                           acquired=time.time() - 3600 if case == 'reused' else None)
                if case == 'shared':
                    self.lease(p.pid, sid='synthetic_other_session')
                if case == 'gateway-cgroup':
                    original = Path.read_text
                    def protected(path, *args, **kwargs):
                        return '0::/system.slice/hermes-gateway.service' if str(path) == f'/proc/{p.pid}/cgroup' else original(path, *args, **kwargs)
                    with patch.object(Path, 'read_text', protected):
                        self.assertIsNone(self.m.get_session_execution(self.sid)['execution'])
                else:
                    self.assertIsNone(self.m.get_session_execution(self.sid)['execution'])
                with self.assertRaises(self.m.ApiError):
                    self.m.stop_session_execution({'session_id': self.sid, 'execution_id': '0' * 64, 'confirm': 'stop-cli'})
                self.assertIsNone(p.poll())

    def test_changed_lease_during_stop_preserves_replacement(self):
        p = self.worker(stubborn=True)
        self.lease(p.pid)
        descriptor = self.m.get_session_execution(self.sid)['execution']
        def change_then_signal(fd, sig):
            self.lease(p.pid, acquired=time.time() + 0.001)
            # Deliberately no real signal: test the pre-next-signal generation guard.
        with patch.object(self.m.signal, 'pidfd_send_signal', change_then_signal):
            with self.assertRaises(self.m.ApiError) as caught:
                self.m.stop_session_execution({'session_id': self.sid, 'execution_id': descriptor['id'], 'confirm': 'stop-cli'})
        self.assertEqual(caught.exception.code, 'execution_changed')
        self.assertIsNone(p.poll())
        with self.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM session_turn_leases').fetchone()[0], 1)

    def test_invalid_confirmation_never_signals_worker(self):
        p = self.worker()
        self.lease(p.pid)
        descriptor = self.m.get_session_execution(self.sid)['execution']
        for body in ({}, {'session_id': self.sid, 'execution_id': descriptor['id']},
                     {'session_id': self.sid, 'execution_id': descriptor['id'], 'confirm': 'yes'},
                     {'session_id': '../state', 'execution_id': descriptor['id'], 'confirm': 'stop-cli'}):
            with self.subTest(body=body), self.assertRaises(self.m.ApiError):
                self.m.stop_session_execution(body)
            self.assertIsNone(p.poll())

    def test_cli_process_exit_does_not_delete_another_session_lease(self):
        p = self.worker(stubborn=True)
        self.lease(p.pid)
        self.lease(2147483647, sid='synthetic_other_session')
        descriptor = self.m.get_session_execution(self.sid)['execution']
        self.m.stop_session_execution({'session_id': self.sid, 'execution_id': descriptor['id'], 'confirm': 'stop-cli'})
        p.wait(timeout=3)
        with self.db() as c:
            self.assertEqual(c.execute('SELECT conversation_id FROM session_turn_leases').fetchall(), [('synthetic_other_session',)])


    def test_authenticated_http_control_stops_only_verified_fixture_worker(self):
        p = self.worker(stubborn=True)
        self.lease(p.pid)
        loop, runner, thread, port = start_panel_core_server(self.m, 'fixture-execution-key')
        def request(method, path, body=None, auth=True):
            with contextlib.closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as conn:
                headers = {'Content-Type': 'application/json'}
                if auth:
                    headers['X-Panel-Key'] = 'fixture-execution-key'
                conn.request(method, path, json.dumps(body) if body is not None else None, headers)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
        try:
            self.assertEqual(request('GET', f'/sessions/execution?session_id={self.sid}', auth=False)[0], 401)
            self.assertIsNone(p.poll())
            status, state = request('GET', f'/sessions/execution?session_id={self.sid}')
            self.assertEqual(status, 200)
            body = {'session_id': self.sid, 'execution_id': state['execution']['id'], 'confirm': 'stop-cli'}
            self.assertEqual(request('POST', '/sessions/execution/stop', body, auth=False)[0], 401)
            self.assertIsNone(p.poll())
            self.assertEqual(request('POST', '/sessions/execution/stop', body), (200, {'status': 'cancelled', 'busy': False}))
            p.wait(timeout=3)
            self.assertEqual(request('GET', f'/sessions/execution?session_id={self.sid}'), (200, {'busy': False, 'execution': None}))
        finally:
            stop_panel_core_server(loop, runner, thread)


if __name__ == '__main__':
    unittest.main()
