"""Live lease contracts over local HTTP only; no native browser/agent access."""
import http.client
import importlib
import json
from pathlib import Path
import subprocess
import threading
from typing import Any
import unittest
from unittest.mock import patch


class LiveControlTests(unittest.TestCase):
    A = "controller-aaaaaaaa-0001"
    B = "controller-bbbbbbbb-0002"

    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module("sidecar.hermes_panel_sidecar")

        class TestHandler(cls.module.Handler):
            key = "fixture-live-control-key"

        cls.key = TestHandler.key
        cls.server = cls.module.ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        if cls.thread.is_alive():
            raise AssertionError("fixture HTTP server did not stop")

    def setUp(self):
        with self.module._LIVE_LOCK:
            self.module._live.clear()
            self.module._live.update(until=0.0)
        self.page = {"type": "page", "url": "https://fixture.invalid",
                     "title": "Fixture", "webSocketDebuggerUrl": "ws://127.0.0.1/fixture"}
        self.page_patch = patch.object(self.module, "_live_page", return_value=self.page)
        self.page_patch.start()
        self.addCleanup(self.page_patch.stop)

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            conn.request(method, path, body=None if body is None else json.dumps(body),
                         headers={"X-Panel-Key": self.key, "Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def takeover(self, client=None, session: Any = ""):
        return self.request("POST", "/live/takeover", {
            "clientId": self.A if client is None else client, "sessionId": session})

    def test_second_controller_cannot_steal_lease(self):
        self.assertEqual(self.takeover()[0], 200)
        status, body = self.takeover(self.B)
        self.assertEqual(status, 409, body)

    def test_state_identifies_only_requesting_controller(self):
        self.takeover()
        for client, expected in ((self.A, True), (self.B, False), (None, False)):
            path = "/live/state" + ("?client_id=" + client if client else "")
            status, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertTrue(body["takenOver"])
            self.assertEqual(body.get("controlledByYou"), expected)
            self.assertGreater(body.get("leaseRemainingSeconds", 0), 0)
            self.assertNotIn("clientId", body)
            self.assertNotIn("ownerClientId", body)
            self.assertNotIn(self.A, json.dumps(body))

    def test_other_controller_cannot_release(self):
        self.takeover()
        self.assertEqual(self.request("POST", "/live/release", {"clientId": self.B})[0], 409)
        self.assertEqual(self.takeover(self.B)[0], 409)
        self.assertEqual(self.request("POST", "/live/release", {"clientId": self.A})[0], 200)
        self.assertEqual(self.takeover(self.B)[0], 200)

    def test_other_controller_cannot_type(self):
        self.takeover()
        with patch.object(self.module, "_CdpSocket") as socket:
            status, body = self.request("POST", "/live/type", {
                "clientId": self.B, "text": "不应输入"})
            self.assertEqual(status, 409)
            self.assertEqual(body["error"]["code"], "live_owned_elsewhere")
            socket.assert_not_called()

    def test_identity_validation_on_mutations(self):
        invalid = (None, "", "short", "x" * 97, "控制器标识" * 5,
                   "a" * 15 + "\n", "a" * 16 + "/", True, 123, [])
        for path in ("takeover", "release", "type"):
            for client in invalid:
                with self.subTest(path=path, client=client):
                    body: dict[str, Any] = {"text": "hello", "sessionId": ""}
                    if client is not None:
                        body["clientId"] = client
                    status, response = self.request("POST", "/live/" + path, body)
                    self.assertEqual(status, 400, response)

    def test_anonymous_observation_and_invalid_identity(self):
        status, body = self.request("GET", "/live/state")
        self.assertEqual(status, 200)
        self.assertFalse(body.get("controlledByYou", True))
        self.assertEqual(body.get("leaseRemainingSeconds"), 0)
        self.assertEqual(self.request("GET", "/live/state?client_id=short")[0], 400)

    def test_expiry_cleans_ownership_and_release_is_idempotent(self):
        with patch.object(self.module.time, "monotonic", return_value=100):
            self.takeover()
        with patch.object(self.module.time, "monotonic", return_value=145):
            _, body = self.request("GET", "/live/state?client_id=" + self.A)
            self.assertFalse(body["takenOver"])
            self.assertFalse(body.get("controlledByYou", True))
            self.assertEqual(body.get("leaseRemainingSeconds"), 0)
            self.assertNotIn("ownerSessionId", body)
            self.assertNotIn("clientId", self.module._live)
            self.assertEqual(self.request("POST", "/live/type", {"clientId": self.A, "text": "hi"})[0], 409)
            self.assertEqual(self.request("POST", "/live/release", {"clientId": self.B})[0], 200)
            self.assertEqual(self.request("POST", "/live/release", {"clientId": self.B})[0], 200)
            self.assertEqual(self.takeover(self.B)[0], 200)

    def test_release_at_expiry_boundary_is_idempotent(self):
        with patch.object(self.module.time, "monotonic", return_value=100):
            self.takeover()
        with patch.object(self.module.time, "monotonic", side_effect=[144.999, 145, 145]):
            self.assertEqual(self.request("POST", "/live/release", {"clientId": self.A})[0], 200)

    def test_expired_owner_release_cannot_clear_new_owner(self):
        with patch.object(self.module.time, "monotonic", return_value=100):
            self.takeover()
        with patch.object(self.module.time, "monotonic", return_value=150):
            self.assertEqual(self.takeover(self.B)[0], 200)
            self.assertEqual(self.request("POST", "/live/release", {"clientId": self.A})[0], 409)
            self.assertTrue(self.request("GET", "/live/state?client_id=" + self.B)[1]["controlledByYou"])

    def test_text_validation_and_browser_asleep_preserved(self):
        self.takeover()
        for text in (None, "", 1, [], "x" * (self.module.MAX_LIVE_TEXT + 1)):
            with self.subTest(text_type=type(text).__name__):
                self.assertEqual(self.request("POST", "/live/type", {"clientId": self.A, "text": text})[0], 400)
        self.request("POST", "/live/release", {"clientId": self.A})
        with patch.object(self.module, "_live_page", return_value=None):
            self.assertEqual(self.takeover()[0], 409)
            _, state = self.request("GET", "/live/state")
            self.assertFalse(state["running"])
            self.assertFalse(state["takenOver"])
            self.assertFalse(state["controlledByYou"])

    def test_terminal_selected_session_permits_takeover(self):
        for status in ("completed", "cancelled", "failed", "interrupted"):
            with self.subTest(status=status), patch.object(self.module, "get_session_run", return_value={"busy": False, "run": {"status": status}}):
                self.assertEqual(self.takeover(session="session_abcdef")[0], 200)

    def test_owner_renewal_extends_monotonic_lease(self):
        with patch.object(self.module.time, "monotonic", return_value=100):
            self.takeover()
        with patch.object(self.module.time, "monotonic", return_value=120):
            self.assertEqual(self.takeover()[0], 200)
        with patch.object(self.module.time, "monotonic", return_value=150):
            _, body = self.request("GET", "/live/state?client_id=" + self.A)
            self.assertTrue(body.get("controlledByYou"))
            self.assertEqual(body.get("leaseRemainingSeconds"), 15)

    def test_selected_busy_or_active_session_rejects_takeover(self):
        cases = [{"run": None, "busy": True}]
        cases += [{"run": {"status": status}, "busy": False}
                  for status in ("queued", "running", "waiting_for_approval", "stopping")]
        for discovery in cases:
            with self.subTest(discovery=discovery), patch.object(
                    self.module, "get_session_run", return_value=discovery) as get_run:
                status, body = self.takeover(session="session_abcdef")
                self.assertEqual(status, 409, body)
                self.assertEqual(body["error"]["code"], "live_session_busy")
                get_run.assert_called_once_with("session_abcdef")
                self.assertFalse(self.request("GET", "/live/state")[1]["takenOver"])

    def test_session_discovery_errors_fail_closed(self):
        for error in (self.module.ApiError(503, "session_run_unavailable", "unavailable"),
                      self.module.ApiError(404, "session_not_found", "missing"), OSError("fixture")):
            with self.subTest(error=error), patch.object(self.module, "get_session_run", side_effect=error):
                status, body = self.takeover(session="session_abcdef")
                self.assertEqual(status, 503, body)
                self.assertEqual(body["error"]["code"], "session_run_unavailable")
                self.assertFalse(self.request("GET", "/live/state")[1]["takenOver"])

    def test_malformed_discovery_fails_closed(self):
        for discovery in (None, {}, {"busy": "false", "run": None},
                          {"busy": False, "run": {}}, {"busy": False, "run": {"status": "unknown"}}):
            with self.subTest(discovery=discovery), patch.object(
                    self.module, "get_session_run", return_value=discovery):
                self.assertEqual(self.takeover(session="session_abcdef")[0], 503)

    def test_renewal_cannot_change_owner_session(self):
        with patch.object(self.module, "get_session_run", return_value={"busy": False, "run": None}):
            self.assertEqual(self.takeover(session="session_abcdef")[0], 200)
            _, body = self.request("GET", "/live/state?client_id=" + self.A)
            self.assertEqual(body.get("ownerSessionId"), "session_abcdef")
            self.assertEqual(self.takeover(session="session_other1")[0], 409)
            self.assertEqual(self.takeover(session="")[0], 409)
            self.assertEqual(self.takeover(session="session_abcdef")[0], 200)

    def test_renewal_rechecks_selected_session(self):
        with patch.object(self.module, "get_session_run", return_value={"busy": False, "run": None}):
            self.assertEqual(self.takeover(session="session_abcdef")[0], 200)
        with patch.object(self.module, "get_session_run", return_value={"busy": True, "run": None}):
            self.assertEqual(self.takeover(session="session_abcdef")[0], 409)

    def test_global_and_omitted_session_skip_discovery(self):
        with patch.object(self.module, "get_session_run", side_effect=AssertionError("must not discover")):
            self.assertEqual(self.request("POST", "/live/takeover", {"clientId": self.A})[0], 200)
            self.assertEqual(self.takeover()[0], 200)
            self.assertNotIn("ownerSessionId", self.request("GET", "/live/state")[1])

    def test_session_validation(self):
        for session in (None, True, 1, [], "x", "session/path", "session_abcdef\n"):
            with self.subTest(session=session), patch.object(self.module, "get_session_run") as get_run:
                self.assertEqual(self.takeover(session=session)[0], 400)
                get_run.assert_not_called()

    def test_type_does_not_hold_lease_lock_during_focus_discovery(self):
        """慢的焦点探测不能拖住 /live/state 与心跳；探测期间交还控制权则不得插入。"""
        self.takeover()
        evaluating = threading.Event()
        proceed = threading.Event()
        calls = []

        class Socket:
            def __init__(self, url):
                pass

            def call(self, method, params=None):
                calls.append((method, params))
                if method == "Runtime.evaluate":
                    evaluating.set()
                    if not proceed.wait(timeout=3):
                        raise AssertionError("input fixture timed out")
                    return {"result": {"value": True}}
                return {}

            def close(self):
                pass

        with patch.object(self.module, "_CdpSocket", Socket):
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                future = pool.submit(self.request, "POST", "/live/type", {"clientId": self.A, "text": "中文\ntext"})
                try:
                    self.assertTrue(evaluating.wait(timeout=3))
                    acquired = self.module._LIVE_LOCK.acquire(blocking=False)
                    if acquired:
                        self.module._LIVE_LOCK.release()
                    self.assertTrue(acquired, "focus discovery must not hold the lease lock")
                    self.assertEqual(self.request("POST", "/live/release", {"clientId": self.A})[0], 200)
                finally:
                    proceed.set()
                status, body = future.result(timeout=3)
        self.assertEqual((status, body["error"]["code"]), (409, "not_taken_over"))
        self.assertNotIn("Input.insertText", [m for m, _ in calls])

    def test_type_inserts_when_lease_still_owned_after_discovery(self):
        self.takeover()
        calls = []

        class Socket:
            def __init__(self, url):
                pass

            def call(self, method, params=None):
                calls.append((method, params))
                return {"result": {"value": True}} if method == "Runtime.evaluate" else {}

            def close(self):
                pass

        with patch.object(self.module, "_CdpSocket", Socket):
            self.assertEqual(self.request("POST", "/live/type", {"clientId": self.A, "text": "中文\ntext"}),
                             (200, {"ok": True, "inserted": True}))
        self.assertEqual(calls[-1], ("Input.insertText", {"text": "中文\ntext"}))

    def test_no_insertion_when_visible_page_cannot_be_verified(self):
        self.takeover()
        self.page_patch.stop()
        with patch.object(self.module, "_cdp_http", return_value=[self.page]), patch.object(
                self.module, "_page_eval", return_value="hidden"), patch.object(self.module, "_CdpSocket") as socket:
            socket.return_value.call.return_value = {"result": {"value": True}}
            status, body = self.request("POST", "/live/type", {"clientId": self.A, "text": "hello"})
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"]["code"], "visible_page_unavailable")
            socket.assert_not_called()

    def test_no_insertion_when_multiple_pages_are_visible(self):
        self.takeover()
        self.page_patch.stop()
        second = {**self.page, "webSocketDebuggerUrl": "ws://127.0.0.1/other"}
        with patch.object(self.module, "_cdp_http", return_value=[self.page, second]), patch.object(
                self.module, "_page_eval", return_value="visible"), patch.object(self.module, "_CdpSocket") as socket:
            socket.return_value.call.return_value = {"result": {"value": True}}
            self.assertEqual(self.request("POST", "/live/type", {"clientId": self.A, "text": "hello"})[0], 409)
            socket.assert_not_called()

    def test_type_rechecks_lease_after_focus_discovery(self):
        self.takeover()
        with patch.object(self.module, "_CdpSocket") as socket:
            def focus(method, params=None):
                self.module._live["until"] = 0
                return {"result": {"value": True}}
            socket.return_value.call.side_effect = focus
            status, body = self.request("POST", "/live/type", {"clientId": self.A, "text": "late"})
            self.assertEqual(status, 409, body)
            self.assertEqual(socket.return_value.call.call_count, 1)

    def test_uncertain_iframe_focus_has_clear_error(self):
        self.takeover()
        with patch.object(self.module, "_CdpSocket") as socket:
            socket.return_value.call.return_value = {"result": {"value": "uncertain"}}
            status, body = self.request("POST", "/live/type", {"clientId": self.A, "text": "no"})
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"]["code"], "focused_frame_unavailable")
            self.assertEqual(socket.return_value.call.call_count, 1)
            socket.return_value.close.assert_called_once()

    def test_visible_page_selection_never_depends_on_target_order(self):
        self.page_patch.stop()
        hidden = {**self.page, "webSocketDebuggerUrl": "ws://127.0.0.1/hidden"}
        with patch.object(self.module, "_cdp_http", return_value=[hidden, self.page]), patch.object(
                self.module, "_page_eval", side_effect=["hidden", "visible"]):
            self.assertEqual(self.module._live_page(require_visible=True), self.page)
        with patch.object(self.module, "_cdp_http", return_value=[hidden]), patch.object(
                self.module, "_page_eval", side_effect=self.module.ApiError(502, "cdp_error", "fixture")):
            self.assertIsNone(self.module._live_page(require_visible=True))
            self.assertEqual(self.module._live_page(), hidden)  # Metadata fallback remains read-only.

    def test_valid_identity_boundaries_and_empty_state_identity(self):
        for client in ("a" * 16, "Z_0-" * 24, "12345678-1234-1234-1234-123456789abc"):
            with self.subTest(client=client):
                self.assertEqual(self.takeover(client)[0], 200)
                self.assertTrue(self.request("GET", "/live/state?client_id=" + client)[1]["controlledByYou"])
                self.assertEqual(self.request("POST", "/live/release", {"clientId": client})[0], 200)
        self.assertEqual(self.request("GET", "/live/state?client_id=")[0], 400)

    def test_noneditable_focus_never_inserts(self):
        self.takeover()
        with patch.object(self.module, "_CdpSocket") as socket:
            socket.return_value.call.return_value = {"result": {"value": False}}
            self.assertEqual(self.request("POST", "/live/type", {"clientId": self.A, "text": "no"}),
                             (200, {"ok": True, "inserted": False}))
            self.assertEqual(socket.return_value.call.call_count, 1)

    def eval_focus(self, fixture):
        script = "const vm = require('node:vm');\n" + fixture + "\nconsole.log(JSON.stringify(vm.runInNewContext(" + json.dumps(self.module._EDITABLE_FOCUS_JS) + ", {document})));"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_same_origin_iframe_recurses_to_actual_focus(self):
        for tag, expected in (("TEXTAREA", True), ("BUTTON", False)):
            with self.subTest(tag=tag):
                fixture = """const child = {hasFocus: () => true, visibilityState: 'visible', activeElement: {tagName: TAG, matches: () => false}};
const document = {hasFocus: () => true, visibilityState: 'visible', activeElement: {tagName: 'IFRAME', contentDocument: child}};""".replace("TAG", json.dumps(tag))
                self.assertEqual(self.eval_focus(fixture), expected)

    def test_cross_origin_iframe_is_uncertain(self):
        for frame in ("{tagName: 'IFRAME', contentDocument: null}",
                      "{tagName: 'IFRAME', get contentDocument() {throw new Error('SecurityError')}}"):
            self.assertEqual(self.eval_focus("const document = {hasFocus: () => true, visibilityState: 'visible', activeElement: " + frame + "};"), "uncertain")

    def test_disabled_readonly_hidden_and_shadow_focus(self):
        elements = [("{tagName: 'TEXTAREA', disabled: true}", False),
                    ("{tagName: 'TEXTAREA', readOnly: true}", False),
                    ("{tagName: 'INPUT', type: 'hidden'}", False),
                    ("{tagName: 'INPUT', type: 'text', matches: () => true}", False),
                    ("{tagName: 'INPUT', type: 'text', matches: () => false}", True),
                    ("{tagName: 'DIV', isContentEditable: true}", True),
                    ("{tagName: 'DIV', shadowRoot: {activeElement: {tagName: 'TEXTAREA'}}}", True)]
        for element, expected in elements:
            with self.subTest(element=element):
                self.assertEqual(self.eval_focus("const document = {hasFocus: () => true, visibilityState: 'visible', activeElement: " + element + "};"), expected)
        self.assertFalse(self.eval_focus("const document = {hasFocus: () => true, visibilityState: 'hidden', activeElement: {tagName: 'TEXTAREA'}};"))
        self.assertFalse(self.eval_focus("const document = {hasFocus: () => false, visibilityState: 'visible', activeElement: {tagName: 'TEXTAREA'}};"))

    def test_client_helpers_send_frozen_identity_contract(self):
        source = Path(__file__).resolve().parents[1] / "src/lib/sidecar.ts"
        script = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const url = code => 'data:text/javascript;base64,' + Buffer.from(code).toString('base64');
globalThis.document = {baseURI: 'https://panel.fixture/'};
globalThis.calls = [];
const mock = url('export class ApiError extends Error {}\nexport function request(...args) {globalThis.calls.push(args);return Promise.resolve({ok:true});}');
const source = stripTypeScriptTypes(fs.readFileSync(process.argv[1], 'utf8')).replace('"./api.js"', JSON.stringify(mock));
const {sidecar} = await import(url(source));
const signal = new AbortController().signal;
const client = 'controller-aaaaaaaa-0001';
await sidecar.liveState(signal, client);
assert.equal(calls[0][0], 'live/state?client_id=' + client);
assert.equal(calls[0][1].signal, signal);
await sidecar.liveState();
assert.equal(calls[1][0], 'live/state');
await sidecar.liveTakeover(client, 'session_abcdef');
assert.equal(calls[2][0], 'live/takeover');
assert.deepEqual(JSON.parse(calls[2][1].body), {clientId:client, sessionId:'session_abcdef'});
await sidecar.liveRelease(client, true);
assert.equal(calls[3][0], 'live/release');
assert.deepEqual(JSON.parse(calls[3][1].body), {clientId:client});
assert.equal(calls[3][1].keepalive, true);
await sidecar.liveRelease(client);
assert.equal(calls[4][1].keepalive, false);
await sidecar.liveType('中文\ntext', client);
assert.equal(calls[5][0], 'live/type');
assert.deepEqual(JSON.parse(calls[5][1].body), {text:'中文\ntext', clientId:client});
for (const call of calls.slice(2)) assert.equal(call[1].method, 'POST');
console.log('client contract passed');
"""
        result = subprocess.run(["node", "--disable-warning=ExperimentalWarning", "--input-type=module", "-e", script, str(source)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("client contract passed", result.stdout)

    def test_concurrent_acquisition_has_exactly_one_owner(self):
        import concurrent.futures
        barrier = threading.Barrier(2)

        def acquire(client):
            barrier.wait(timeout=3)
            return client, self.takeover(client)[0]

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(acquire, (self.A, self.B)))
        self.assertEqual(sorted(status for _, status in results), [200, 409])
        winner = next(client for client, status in results if status == 200)
        self.assertTrue(self.request("GET", "/live/state?client_id=" + winner)[1].get("controlledByYou"))


if __name__ == "__main__":
    unittest.main()
