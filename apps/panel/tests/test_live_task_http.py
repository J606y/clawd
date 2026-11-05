"""Real isolated sidecar HTTP + SQLite ownership + Chromium CDP.

No production endpoints or model runs. Browser uses an ephemeral profile/port.
"""
import base64
import errno
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from sidecar import hermes_panel_sidecar as sc
from native_browser_fixture import bind_profile, install_native, register_page


class TaskLiveHttpIntegrationTests(unittest.TestCase):
    def test_owned_frame_over_real_http_preserves_unrelated_page(self):
        scratch = os.environ.get('TMPDIR', '/root/hermes-workspace')
        with tempfile.TemporaryDirectory(prefix='live-http-', dir=scratch) as temp:
            root = Path(temp)
            profile = root / 'browser'
            db = root / 'state' / 'tasks.sqlite3'
            proc = subprocess.Popen(['/usr/bin/chromium', '--headless=new', '--no-sandbox', '--disable-gpu',
                                     '--disable-dev-shm-usage', '--remote-debugging-port=0',
                                     '--user-data-dir=' + str(profile), 'about:blank'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            server = None
            browser = None
            thread = None
            try:
                for _ in range(100):
                    if (profile / 'DevToolsActivePort').exists():
                        break
                    if proc.poll() is not None:
                        self.fail('isolated Chromium exited before readiness')
                    time.sleep(0.05)
                port = int((profile / 'DevToolsActivePort').read_text().splitlines()[0])
                host = '127.0.0.1:' + str(port)
                def http_cdp(path):
                    with urllib.request.urlopen('http://' + host + path, timeout=3) as response:
                        return json.load(response)
                version = http_cdp('/json/version')
                browser_id = urlsplit(version['webSocketDebuggerUrl']).path
                browser = sc._CdpSocket(version['webSocketDebuggerUrl'])
                own = browser.call('Target.createTarget', {'url': 'about:blank'})['targetId']
                other = browser.call('Target.createTarget', {'url': 'about:blank'})['targetId']
                pages = http_cdp('/json/list')
                for tid, title in [(own, 'AI Owned Test Page'), (other, 'Private Unrelated Test Page')]:
                    ws = sc._CdpSocket(next(p['webSocketDebuggerUrl'] for p in pages if p['id'] == tid))
                    try:
                        ws.call('Page.navigate', {'url': 'data:text/html,<title>' + title + '</title><body style="background:teal;color:white">' + title + '</body>'})
                        for _ in range(50):
                            current = ws.call('Runtime.evaluate', {'expression': 'document.title', 'returnByValue': True})
                            if current.get('result', {}).get('value') == title:
                                break
                            time.sleep(0.02)
                        self.assertEqual(current['result']['value'], title)
                    finally:
                        ws.close()
                browser.call('Target.activateTarget', {'targetId': other})
                bind_profile(self, root / "profile")
                install_native(sc, self)
                register_page(task_id="fixture-task", session_id="fixture-session", turn_id="fixture-turn",
                              target_id=own, browser_id=browser_id, endpoint="http://" + host)
                self.assertFalse(db.exists(), "native spectator must not open the shadow task db")
                class IsolatedHandler(sc.Handler):
                    key = 'isolated-fixture-key'
                server = ThreadingHTTPServer(('127.0.0.1', 0), IsolatedHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = 'http://127.0.0.1:' + str(server.server_port)
                url = base + '/live/task?session_id=fixture-session&task_id=fixture-task'
                with patch.object(sc, 'LIVE_CDP', host), patch.object(sc, 'LIVE_TASK_DB', db):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(url, timeout=5)
                    self.assertEqual(error.exception.code, 401)
                    request = urllib.request.Request(url, headers={'X-Panel-Key': 'isolated-fixture-key'})
                    with urllib.request.urlopen(request, timeout=8) as response:
                        self.assertEqual(response.headers['Cache-Control'], 'no-store')
                        frame = json.load(response)
                    self.assertEqual(frame['targetId'], own)
                    self.assertEqual(frame['title'], 'AI Owned Test Page')
                    self.assertEqual(frame['status'], 'active')
                    image = base64.b64decode(frame['image'].split(',', 1)[1], validate=True)
                    self.assertTrue(image.startswith(b'\xff\xd8\xff'))
                    self.assertTrue(image.endswith(b'\xff\xd9'))
                    self.assertGreater(len(image), 1000)

                    # Exercise real HTTP /live/task/control and /live/task/input against owned target while 'other' is active
                    own_ws = sc._CdpSocket(next(p['webSocketDebuggerUrl'] for p in pages if p['id'] == own))
                    other_ws = sc._CdpSocket(next(p['webSocketDebuggerUrl'] for p in pages if p['id'] == other))
                    try:
                        own_ws.call('Runtime.evaluate', {
                            'expression': '''document.body.innerHTML = '<input id="in" style="position:fixed;left:0;top:0;width:400px;height:200px" />'; true'''
                        })
                        other_ws.call('Runtime.evaluate', {
                            'expression': '''document.body.innerHTML = '<input id="other_in" style="position:fixed;left:0;top:0;width:400px;height:200px" value="untouched" />'; true'''
                        })
                        client_id = 'controller-http-real-0001'
                        def post_json(path, payload):
                            req = urllib.request.Request(
                                base + path,
                                data=json.dumps(payload).encode('utf-8'),
                                headers={'X-Panel-Key': 'isolated-fixture-key', 'Content-Type': 'application/json'},
                                method='POST',
                            )
                            with urllib.request.urlopen(req, timeout=8) as resp:
                                return json.load(resp)

                        with patch.object(sc, 'get_session_run', return_value={'busy': False, 'run': {'status': 'completed'}}):
                            ctrl_res = post_json('/live/task/control', {
                                'action': 'takeover',
                                'clientId': client_id,
                                'sessionId': 'fixture-session',
                                'taskId': 'fixture-task',
                                'targetId': own,
                            })
                            self.assertTrue(ctrl_res['controlledByYou'])
                            post_json('/live/task/input', {
                                'clientId': client_id,
                                'sessionId': 'fixture-session',
                                'taskId': 'fixture-task',
                                'targetId': own,
                                'kind': 'click',
                                'xRatio': 0.05,
                                'yRatio': 0.05,
                            })
                            post_json('/live/task/input', {
                                'clientId': client_id,
                                'sessionId': 'fixture-session',
                                'taskId': 'fixture-task',
                                'targetId': own,
                                'kind': 'text',
                                'text': 'OwnedInput123',
                            })
                            post_json('/live/task/input', {
                                'clientId': client_id,
                                'sessionId': 'fixture-session',
                                'taskId': 'fixture-task',
                                'targetId': own,
                                'kind': 'key',
                                'key': 'Backspace',
                            })
                            own_val = own_ws.call('Runtime.evaluate', {'expression': 'document.getElementById("in").value', 'returnByValue': True})['result']['value']
                            other_val = other_ws.call('Runtime.evaluate', {'expression': 'document.getElementById("other_in").value', 'returnByValue': True})['result']['value']
                            self.assertEqual(own_val, 'OwnedInput12')
                            self.assertEqual(other_val, 'untouched')
                            rel_res = post_json('/live/task/control', {
                                'action': 'release',
                                'clientId': client_id,
                                'sessionId': 'fixture-session',
                                'taskId': 'fixture-task',
                                'targetId': own,
                            })
                            self.assertFalse(rel_res['controlledByYou'])
                    finally:
                        own_ws.close()
                        other_ws.close()

                    from tools.browser_task_registry import _mark_ended
                    browser.call('Target.closeTarget', {'targetId': own})
                    _mark_ended('fixture-task', '')
                    with urllib.request.urlopen(request, timeout=5) as response:
                        ended = json.load(response)
                    self.assertEqual(ended['status'], 'ended')
                    self.assertIsNone(ended['image'])
                    remaining = {p['id'] for p in http_cdp('/json/list')}
                    self.assertNotIn(own, remaining)
                    self.assertIn(other, remaining)
            finally:
                if server:
                    server.shutdown()
                    server.server_close()
                if thread:
                    thread.join(timeout=3)
                if browser:
                    browser.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                deadline = time.monotonic() + 3
                while profile.exists():
                    try:
                        shutil.rmtree(profile)
                    except OSError as exc:
                        if exc.errno != errno.ENOTEMPTY or time.monotonic() >= deadline:
                            raise
                        time.sleep(.05)


if __name__ == '__main__':
    unittest.main()
