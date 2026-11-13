"""Task-only spectator API: synthetic registry/CDP, never production browsers."""
import sys as _sys
from pathlib import Path as _Path
_REPO_ROOT = _Path(__file__).resolve().parents[3]
_TESTS_DIR = _Path(__file__).resolve().parent
if str(_TESTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.append(str(_REPO_ROOT))

import base64
import io
import json
import tempfile
import os
import unittest
from unittest.mock import Mock, patch

from clawd_server import panel_core as sc


class TaskLiveApiTests(unittest.TestCase):
    def test_standalone_deployment_can_load_installed_plugin_store(self):
        """Native install retires the shadow reader. It must not open PANEL_LIVE_TASK_DB."""
        import tempfile
        from pathlib import Path
        from native_browser_fixture import bind_profile, install_native
        from clawd_server.live_task_store import ShadowStoreRetired, Store

        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as temp:
            home = Path(temp) / "profile"
            db = Path(temp) / "state/tasks.sqlite3"
            with self.assertRaises(ShadowStoreRetired):
                Store(db)
            self.assertFalse(db.exists())
            bind_profile(self, home)
            install_native(sc, self)
            with self.assertRaises(sc.ApiError) as caught:
                sc._live_task_store()
            self.assertEqual(caught.exception.status, 503)
            self.assertEqual(caught.exception.code, "live_registry_retired")
            self.assertFalse(db.exists())

    def test_missing_registry_waits_without_touching_global_browser(self):
        self.assertTrue(callable(getattr(sc, 'live_task', None)), 'task-only spectator endpoint is missing')
        with patch.object(sc, '_live_task_store', return_value=None), patch.object(sc, '_cdp_http') as cdp:
            result = sc.live_task('session-a')
        self.assertEqual(result['status'], 'waiting')
        self.assertIsNone(result['image'])
        cdp.assert_not_called()

    def fixture(self):
        task = {'taskId': 'task-a', 'sessionId': 'session-a', 'status': 'active', 'targetId': 'OWN',
                'browserId': '/devtools/browser/test-instance', 'cleanupError': '', 'updatedAt': 1.0}
        store = Mock()
        store.latest.return_value = task
        store.owns.return_value = True
        store.control_ready.return_value = True
        image = base64.b64encode(b'\xff\xd8\xffsynthetic-jpeg\xff\xd9').decode()
        ws = Mock()
        ws.call.return_value = {'data': image}
        def cdp(path):
            if path == '/json/version':
                return {'webSocketDebuggerUrl': 'ws://127.0.0.1:9223/devtools/browser/test-instance'}
            if path == '/json/list':
                return [dict(id='OTHER', type='page', title='Private other', url='https://other.test', webSocketDebuggerUrl='ws://127.0.0.1:9223/devtools/page/OTHER'),
                        dict(id='OWN', type='page', title='AI page', url='https://ai.test', webSocketDebuggerUrl='ws://127.0.0.1:9223/devtools/page/OWN')]
            raise AssertionError(path)
        return task, store, ws, cdp, image

    def test_active_captures_only_owned_target(self):
        task, store, ws, cdp, image = self.fixture()
        with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http', side_effect=cdp), patch.object(sc, '_CdpSocket', return_value=ws) as socket:
            result = sc.live_task('session-a', 'task-a')
        self.assertEqual(result['image'], 'data:image/jpeg;base64,' + image)
        self.assertEqual(result['title'], 'AI page')
        self.assertEqual(result['targetId'], 'OWN')
        self.assertEqual(result['status'], 'active')
        socket.assert_called_once_with('ws://127.0.0.1:9223/devtools/page/OWN', timeout=3.0)
        self.assertEqual(ws.call.call_args.args[0], 'Page.captureScreenshot')
        ws.close.assert_called_once()
        store.latest.assert_any_call('session-a', 'task-a')
        store.owns.assert_called_with('session-a', 'task-a', 'OWN', task['browserId'])

    def test_ended_never_connects_to_browser(self):
        task, store, ws, cdp, _ = self.fixture()
        task['status'] = 'ended'
        task['cleanupError'] = 'some windows could not be closed'
        with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http') as http:
            result = sc.live_task('session-a', 'task-a')
        self.assertEqual(result['status'], 'ended')
        self.assertIsNone(result['image'])
        self.assertIsNone(result['targetId'])
        self.assertTrue(result['cleanupError'])
        http.assert_not_called()

    def test_foreign_session_is_rejected_before_browser_access(self):
        task, store, *_ = self.fixture()
        with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http') as http:
            with self.assertRaises(sc.ApiError) as error:
                sc.live_task('session-b', 'task-a')
        self.assertEqual(error.exception.status, 409)
        http.assert_not_called()

    def test_target_must_belong_to_this_task(self):
        _, store, ws, cdp, _ = self.fixture()
        store.owns.return_value = False
        with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http', side_effect=cdp), patch.object(sc, '_CdpSocket') as socket:
            with self.assertRaises(sc.ApiError):
                sc.live_task('session-a', 'task-a')
        socket.assert_not_called()

    def test_browser_restart_never_reuses_target(self):
        task, store, ws, cdp, _ = self.fixture()
        task['browserId'] = '/devtools/browser/old-instance'
        with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http', side_effect=cdp), patch.object(sc, '_CdpSocket') as socket:
            with self.assertRaises(sc.ApiError):
                sc.live_task('session-a', 'task-a')
        socket.assert_not_called()

    def test_missing_owned_page_does_not_fall_back_to_other_page(self):
        task, store, ws, cdp, _ = self.fixture()
        task['targetId'] = 'CLOSED'
        with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http', side_effect=cdp), patch.object(sc, '_CdpSocket') as socket:
            result = sc.live_task('session-a', 'task-a')
        self.assertIsNone(result['image'])
        self.assertEqual(result['status'], 'waiting')
        socket.assert_not_called()

    def test_task_ending_during_capture_discards_frame(self):
        task, store, ws, cdp, _ = self.fixture()
        ended = dict(task, status='ended')
        store.latest.side_effect = [task, ended]
        with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http', side_effect=cdp), patch.object(sc, '_CdpSocket', return_value=ws):
            result = sc.live_task('session-a', 'task-a')
        self.assertEqual(result['status'], 'ended')
        self.assertIsNone(result['image'])

    def test_malformed_frame_is_not_sent_to_client(self):
        _, store, ws, cdp, _ = self.fixture()
        for data in ['not-base64!', base64.b64encode(b'<html>not jpeg</html>').decode(), 'A' * (3 * 1024 * 1024 + 1)]:
            with self.subTest(data_length=len(data)):
                ws.call.return_value = {'data': data}
                with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http', side_effect=cdp), patch.object(sc, '_CdpSocket', return_value=ws):
                    with self.assertRaises(sc.ApiError):
                        sc.live_task('session-a', 'task-a')

    def test_capture_failure_closes_socket(self):
        _, store, ws, cdp, _ = self.fixture()
        ws.call.side_effect = OSError('synthetic CDP offline')
        with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http', side_effect=cdp), patch.object(sc, '_CdpSocket', return_value=ws):
            with self.assertRaises(sc.ApiError) as error:
                sc.live_task('session-a', 'task-a')
        self.assertEqual(error.exception.status, 502)
        ws.close.assert_called_once()

    def test_bad_ids_never_open_store(self):
        for sid, tid in [('', ''), ('../other', ''), ('session-a', 'x\n')]:
            with self.subTest(sid=sid, tid=tid), patch.object(sc, '_live_task_store') as store:
                with self.assertRaises(sc.ApiError) as error:
                    sc.live_task(sid, tid)
                self.assertEqual(error.exception.status, 400)
                store.assert_not_called()

    def test_initial_lookup_does_not_bind_previous_ended_turn(self):
        task, store, *_ = self.fixture()
        task['status'] = 'ended'
        with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http') as http:
            result = sc.live_task('session-a')
        self.assertEqual(result['status'], 'waiting')
        self.assertEqual(result['taskId'], '')
        self.assertIsNone(result['image'])
        http.assert_not_called()

    def test_socket_address_cannot_point_to_other_target_or_port(self):
        task, store, ws, cdp, _ = self.fixture()
        for url in ['ws://127.0.0.1:9223/devtools/page/OTHER', 'ws://127.0.0.1:9224/devtools/page/OWN',
                    'ws://example.test:9223/devtools/page/OWN', 'ws://user:password@127.0.0.1:9223/devtools/page/OWN']:
            def changed(path):
                data = cdp(path)
                if path == '/json/list':
                    data[1]['webSocketDebuggerUrl'] = url
                return data
            with self.subTest(url=url), patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_cdp_http', side_effect=changed), patch.object(sc, '_CdpSocket') as socket:
                with self.assertRaises(sc.ApiError):
                    sc.live_task('session-a', 'task-a')
                socket.assert_not_called()

    def test_busy_frame_capacity_fails_without_cdp_requests(self):
        _, store, *_ = self.fixture()
        slots = Mock()
        slots.acquire.return_value = False
        with patch.object(sc, '_live_task_store', return_value=store), patch.object(sc, '_LIVE_FRAME_SLOTS', slots), patch.object(sc, '_cdp_http') as http:
            with self.assertRaises(sc.ApiError) as error:
                sc.live_task('session-a', 'task-a')
        self.assertEqual(error.exception.status, 503)
        slots.release.assert_not_called()
        http.assert_not_called()

    def test_authenticated_dispatch_has_no_store_header_and_unauthenticated_is_denied(self):
        for key, expected in [('fixture-key', 200), ('wrong-key', 401)]:
            handler = object.__new__(sc.Handler)
            handler.headers = {'X-Panel-Key': key}
            handler.key = 'fixture-key'
            handler.path = '/live/task?session_id=session-a'
            handler.close_connection = False
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler.wfile = io.BytesIO()
            with patch.object(sc, 'live_task', return_value={'status': 'waiting', 'image': None}) as live:
                handler._dispatch('GET')
            handler.send_response.assert_called_once_with(expected)
            if expected == 200:
                live.assert_called_once_with('session-a', '')
                self.assertIn(('Cache-Control', 'no-store'), [x.args for x in handler.send_header.call_args_list])
                self.assertIsNone(json.loads(handler.wfile.getvalue())['image'])
            else:
                live.assert_not_called()

    def test_task_control_and_input_contracts(self):
        self.assertTrue(callable(getattr(sc, 'live_task_control', None)), 'live_task_control endpoint is missing')
        self.assertTrue(callable(getattr(sc, 'live_task_input', None)), 'live_task_input endpoint is missing')

        task, store, ws, cdp, _ = self.fixture()
        client_a = 'controller-aaaaaaaa-0001'
        client_b = 'controller-bbbbbbbb-0002'
        # Real cross-process locking is covered separately in test_live_task_pause.
        self.enterContext(patch.object(sc, '_live_task_open_gate', side_effect=lambda: tempfile.TemporaryFile(dir=os.environ.get('TMPDIR'))))

        with sc._LIVE_LOCK:
            sc._live.clear()
            sc._live['until'] = 0.0
            if hasattr(sc, '_live_task_lease'):
                sc._live_task_lease.clear()
                sc._live_task_lease['until'] = 0.0

        # 1. Active runs may be controlled only after the browser gate is held.
        for discovery in [{'busy': True, 'run': None},
                          {'busy': False, 'run': {'status': 'running'}},
                          {'busy': False, 'run': {'status': 'queued'}},
                          {'busy': False, 'run': {'status': 'waiting_for_approval'}},
                          {'busy': False, 'run': {'status': 'stopping'}}]:
            with self.subTest(discovery=discovery), patch.object(sc, '_live_task_store', return_value=store), \
                    patch.object(sc, 'get_session_run', return_value=discovery), \
                    patch.object(sc, '_cdp_http', side_effect=cdp):
                result = sc.live_task_control({'action': 'takeover', 'clientId': client_a, 'sessionId': 'session-a',
                                              'taskId': 'task-a', 'targetId': 'OWN'})
                self.assertTrue(result['controlledByYou'])
                sc.live_task_control({'action': 'release', 'clientId': client_a})

        # 2. When session run is idle (busy=False, run terminal/None), takeover succeeds on active owned target
        idle_run = {'busy': False, 'run': {'status': 'completed'}}
        with patch.object(sc, '_live_task_store', return_value=store), \
                patch.object(sc, 'get_session_run', return_value=idle_run), \
                patch.object(sc, '_cdp_http', side_effect=cdp), \
                patch.object(sc, '_CdpSocket', return_value=ws):
            res = sc.live_task_control({'action': 'takeover', 'clientId': client_a, 'sessionId': 'session-a',
                                        'taskId': 'task-a', 'targetId': 'OWN'})
            self.assertEqual(res['ok'], True)
            self.assertEqual(res['controlledByYou'], True)
            self.assertEqual(res['takenOver'], True)
            self.assertGreater(res['leaseRemainingSeconds'], 0)

            # Frame read for client_a shows controlledByYou=True, for client_b shows controlledByYou=False, takenOver=True
            frame_a = sc.live_task('session-a', 'task-a', client_a)
            self.assertEqual(frame_a['controlledByYou'], True)
            self.assertEqual(frame_a['takenOver'], True)
            self.assertNotIn(client_a, json.dumps(frame_a))
            frame_b = sc.live_task('session-a', 'task-a', client_b)
            self.assertEqual(frame_b['controlledByYou'], False)
            self.assertEqual(frame_b['takenOver'], True)
            self.assertNotIn(client_a, json.dumps(frame_b))

            # Client B cannot steal lease or release Client A's lease
            with self.assertRaises(sc.ApiError) as err:
                sc.live_task_control({'action': 'takeover', 'clientId': client_b, 'sessionId': 'session-a',
                                      'taskId': 'task-a', 'targetId': 'OWN'})
            self.assertEqual(err.exception.status, 409)
            self.assertEqual(err.exception.code, 'live_owned_elsewhere')

            with self.assertRaises(sc.ApiError) as err:
                sc.live_task_control({'action': 'release', 'clientId': client_b, 'sessionId': 'session-a',
                                      'taskId': 'task-a', 'targetId': 'OWN'})
            self.assertEqual(err.exception.status, 409)
            self.assertEqual(err.exception.code, 'live_owned_elsewhere')

            # Global live takeover is mutually exclusive while task lease is held by client_b
            with patch.object(sc, '_live_page', return_value={'url': 'https://a', 'title': 'A'}):
                with self.assertRaises(sc.ApiError) as err:
                    sc.live_takeover({'clientId': client_b, 'sessionId': 'session-a'})
                self.assertEqual(err.exception.status, 409)
                self.assertEqual(err.exception.code, 'live_owned_elsewhere')

            # Input operations: click, scroll, text, key (Enter, Backspace, Tab, Escape)
            ws.call.reset_mock()
            def ws_side_effect(method, params=None):
                if method == 'Page.getLayoutMetrics':
                    return {'cssVisualViewport': {'clientWidth': 1280, 'clientHeight': 720}}
                if method == 'Runtime.evaluate':
                    return {'result': {'value': True}}
                return {}
            ws.call.side_effect = ws_side_effect

            click_res = sc.live_task_input({'clientId': client_a, 'sessionId': 'session-a', 'taskId': 'task-a',
                                            'targetId': 'OWN', 'kind': 'click', 'xRatio': 0.5, 'yRatio': 0.25})
            self.assertEqual(click_res['ok'], True)
            methods = [c.args[0] for c in ws.call.call_args_list]
            self.assertIn('Input.dispatchMouseEvent', methods)
            mouse_calls = [c for c in ws.call.call_args_list if c.args[0] == 'Input.dispatchMouseEvent']
            self.assertEqual(mouse_calls[0].args[1]['x'], 640.0)
            self.assertEqual(mouse_calls[0].args[1]['y'], 180.0)

            ws.call.reset_mock()
            scroll_res = sc.live_task_input({'clientId': client_a, 'sessionId': 'session-a', 'taskId': 'task-a',
                                             'targetId': 'OWN', 'kind': 'scroll', 'xRatio': 0.5, 'yRatio': 0.5,
                                             'deltaX': 0, 'deltaY': 240})
            self.assertEqual(scroll_res['ok'], True)
            wheel_calls = [c for c in ws.call.call_args_list if c.args[0] == 'Input.dispatchMouseEvent']
            self.assertEqual(wheel_calls[0].args[1]['type'], 'mouseWheel')
            self.assertEqual(wheel_calls[0].args[1]['deltaY'], 240.0)

            ws.call.reset_mock()
            text_res = sc.live_task_input({'clientId': client_a, 'sessionId': 'session-a', 'taskId': 'task-a',
                                           'targetId': 'OWN', 'kind': 'text', 'text': 'Hello 123'})
            self.assertEqual(text_res, {'ok': True, 'inserted': True})
            insert_calls = [c for c in ws.call.call_args_list if c.args[0] == 'Input.insertText']
            self.assertEqual(len(insert_calls), 1)
            self.assertEqual(insert_calls[0].args[1]['text'], 'Hello 123')

            for key_name in ('Enter', 'Backspace', 'Tab', 'Escape'):
                ws.call.reset_mock()
                key_res = sc.live_task_input({'clientId': client_a, 'sessionId': 'session-a', 'taskId': 'task-a',
                                              'targetId': 'OWN', 'kind': 'key', 'key': key_name})
                self.assertEqual(key_res['ok'], True)
                key_calls = [c for c in ws.call.call_args_list if c.args[0] == 'Input.dispatchKeyEvent']
                self.assertEqual(len(key_calls), 2)
                self.assertEqual(key_calls[0].args[1]['key'], key_name)

            # Reject disallowed keys
            with self.assertRaises(sc.ApiError) as err:
                sc.live_task_input({'clientId': client_a, 'sessionId': 'session-a', 'taskId': 'task-a',
                                    'targetId': 'OWN', 'kind': 'key', 'key': 'F12'})
            self.assertEqual(err.exception.status, 400)

            # Task ending race during input must abort and clear lease
            ended_task = dict(task, status='ended')
            store.latest.side_effect = [task, ended_task]
            ws.call.reset_mock()
            with self.assertRaises(sc.ApiError) as err:
                sc.live_task_input({'clientId': client_a, 'sessionId': 'session-a', 'taskId': 'task-a',
                                    'targetId': 'OWN', 'kind': 'text', 'text': 'ShouldNotInsert'})
            self.assertEqual(err.exception.status, 409)
            self.assertEqual([c for c in ws.call.call_args_list if c.args[0] == 'Input.insertText'], [])
            store.latest.side_effect = None
            store.latest.return_value = task

            # Re-acquire and release by owner
            sc.live_task_control({'action': 'takeover', 'clientId': client_a, 'sessionId': 'session-a',
                                  'taskId': 'task-a', 'targetId': 'OWN'})
            rel = sc.live_task_control({'action': 'release', 'clientId': client_a, 'sessionId': 'session-a',
                                        'taskId': 'task-a', 'targetId': 'OWN'})
            self.assertEqual(rel, {'ok': True, 'controlledByYou': False})
            # Idempotent second release
            rel2 = sc.live_task_control({'action': 'release', 'clientId': client_a})
            self.assertEqual(rel2['ok'], True)


if __name__ == '__main__':
    unittest.main()
