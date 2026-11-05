"""Isolated real CDP navigation -> HTTP spectator -> exact turn finalizer.

Synthetic helpers implement actual page-attached CDP calls, not the installed
Hermes harness. No model call, production browser or plugin installation.
"""
import importlib.util
import json
import os
import shutil
import errno
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from sidecar import hermes_panel_sidecar as sc
from sidecar.live_task_store import Store

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('guard_chain', ROOT / 'plugins/browser-window-guard/__init__.py')
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class Hooks:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback


class TaskLiveChainTests(unittest.TestCase):
    def test_navigation_spectator_and_native_finalizer_preserve_unrelated_targets(self):
        with tempfile.TemporaryDirectory(prefix='live-chain-', dir=os.environ.get('TMPDIR', '/root/hermes-workspace')) as tmp:
            root = Path(tmp)
            profile = root / 'profile'
            db = root / 'state/tasks.sqlite3'
            proc = subprocess.Popen(['/usr/bin/chromium', '--headless=new', '--no-sandbox', '--disable-gpu',
                                     '--disable-dev-shm-usage', '--remote-debugging-port=0',
                                     '--user-data-dir=' + str(profile), 'about:blank'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            server = thread = browser = None
            try:
                deadline = time.monotonic() + 8
                port_file = profile / 'DevToolsActivePort'
                while True:
                    lines = port_file.read_text().splitlines() if port_file.exists() else []
                    if len(lines) >= 2 and lines[0].isdigit() and lines[1].startswith('/devtools/browser/'):
                        break
                    if proc.poll() is not None or time.monotonic() > deadline:
                        self.fail('isolated Chromium did not publish a complete debug endpoint')
                    time.sleep(.02)
                host = '127.0.0.1:' + lines[0]

                def cdp_http(path):
                    with urllib.request.urlopen('http://' + host + path, timeout=3) as response:
                        return json.load(response)

                version = cdp_http('/json/version')
                browser = sc._CdpSocket(version['webSocketDebuggerUrl'])
                user_target = cdp_http('/json/list')[0]['id']
                helpers = '''
import json, urllib.request
from sidecar.hermes_panel_sidecar import _CdpSocket
host = HOST
browser = _CdpSocket(BROWSER_URL)
current = USER_TARGET
page = None

def switch_tab(target):
    global current, page
    with urllib.request.urlopen('http://' + host + '/json/list', timeout=3) as r:
        pages = json.load(r)
    record = next(p for p in pages if p['id'] == target)
    if page is not None:
        page.close()
    page = _CdpSocket(record['webSocketDebuggerUrl'])
    current = target
    browser.call('Target.activateTarget', {'targetId': target})

def cdp(method, **kwargs):
    if method.startswith(('Target.', 'Browser.')):
        return browser.call(method, kwargs)
    return page.call(method, kwargs)

def goto_url(url):
    result = cdp('Page.navigate', url=url)
    if result.get('errorText'):
        raise RuntimeError(result['errorText'])
    return result

def js(expression):
    result = cdp('Runtime.evaluate', expression=expression, returnByValue=True)
    if 'exceptionDetails' in result:
        raise RuntimeError('JavaScript execution failed')
    return result['result'].get('value')

switch_tab(USER_TARGET)
'''.replace('HOST', repr(host)).replace('BROWSER_URL', repr(version['webSocketDebuggerUrl'])).replace('USER_TARGET', repr(user_target))
                env = dict(os.environ, PANEL_LIVE_TASK_DB=str(db), HERMES_LIVE_CDP_HOST=host,
                           PANEL_LIVE_STORE_MODULE=str(ROOT / 'sidecar/live_task_store.py'))
                with patch.dict(os.environ, env):
                    from native_browser_fixture import bind_profile, install_native, register_page
                    from tools.browser_task_cdp import create_page
                    from tools.browser_task_registry import close_owned, session_view
                    from urllib.parse import urlsplit
                    ctx = Hooks()
                    guard.register(ctx)
                    self.assertNotIn('pre_llm_call', ctx.hooks)
                    self.assertNotIn('on_session_end', ctx.hooks)
                    hook = ctx.hooks.get('pre_tool_call')
                    self.assertIsNone(hook(tool_name='browser_exec', args={'code': '# test', 'session': 'named'}, session_id='s', turn_id='t'))
                    bind_profile(self, root / 'profile')
                    install_native(sc, self)
                    os.environ.pop('PANEL_LIVE_TASK_DB', None)
                    self.assertFalse(db.exists())
                    browser_id = urlsplit(version['webSocketDebuggerUrl']).path
                    endpoint = 'http://' + host
                    created = {}
                    for session, turn, title in [('session-a', 'turn-a', 'Owned A'), ('session-b', 'turn-b', 'Owned B')]:
                        target = create_page(endpoint)
                        created[session] = target
                        page = sc._CdpSocket(next(item['webSocketDebuggerUrl'] for item in cdp_http('/json/list') if item['id'] == target))
                        try:
                            page.call('Page.navigate', {'url': 'data:text/html,<title>' + title + '</title><h1>' + title + '</h1>'})
                            for _ in range(50):
                                current = page.call('Runtime.evaluate', {'expression': 'document.title', 'returnByValue': True})
                                if current.get('result', {}).get('value') == title:
                                    break
                                time.sleep(0.02)
                        finally:
                            page.close()
                        register_page(task_id=turn, session_id=session, turn_id=turn, target_id=target, browser_id=browser_id, endpoint=endpoint)
                    a = session_view('session-a', 'turn-a')
                    b = session_view('session-b', 'turn-b')
                    self.assertIsNotNone(a)
                    self.assertIsNotNone(b)
                    all_ids = {item['id'] for item in cdp_http('/json/list') if item.get('type') == 'page'}
                    self.assertEqual(all_ids, {user_target, a['targetId'], b['targetId']})
                    browser.call('Target.activateTarget', {'targetId': user_target})
                    class Handler(sc.Handler):
                        key = 'isolated-chain-key'
                    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    url = 'http://127.0.0.1:' + str(server.server_port) + '/live/task?session_id=session-a&task_id=' + a['taskId']
                    request = urllib.request.Request(url, headers={'X-Panel-Key': Handler.key})
                    with urllib.request.urlopen(request, timeout=8) as response:
                        frame = json.load(response)
                    self.assertEqual(frame['title'], 'Owned A')
                    self.assertEqual(frame['targetId'], a['targetId'])
                    self.assertTrue(frame['image'].startswith('data:image/jpeg;base64,'))
                    control_url = url.split('/live/task?')[0] + '/live/task/control'
                    input_url = url.split('/live/task?')[0] + '/live/task/input'
                    owner = dict(clientId='chain-controller-aaaaaaaa', sessionId='session-a', taskId=a['taskId'], targetId=a['targetId'])

                    def post(endpoint_url, body):
                        req = urllib.request.Request(endpoint_url, data=json.dumps(body).encode(),
                            headers={'X-Panel-Key': Handler.key, 'Content-Type': 'application/json'})
                        with urllib.request.urlopen(req, timeout=6) as response:
                            return json.load(response)

                    self.assertTrue(post(control_url, dict(owner, action='takeover'))['controlledByYou'])
                    post(input_url, dict(owner, kind='text', text='HumanOwnsThisPage'))
                    self.assertEqual(session_view('session-a', a['taskId'])['status'], 'active')
                    self.assertEqual({item['id'] for item in cdp_http('/json/list') if item.get('type') == 'page'}, all_ids)
                    post(control_url, dict(owner, action='release'))
                    close_owned(a['taskId'])
                    with urllib.request.urlopen(request, timeout=6) as response:
                        ended = json.load(response)
                    self.assertEqual(ended['status'], 'ended')
                    self.assertIsNone(ended['image'])
                    self.assertEqual({item['id'] for item in cdp_http('/json/list') if item.get('type') == 'page'}, {user_target, b['targetId']})
                    close_owned(b['taskId'])
                    self.assertEqual({item['id'] for item in cdp_http('/json/list') if item.get('type') == 'page'}, {user_target})
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
                # Chromium children may finish profile writes just after the
                # parent exits. Retry only ENOTEMPTY, within a bounded deadline.
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
