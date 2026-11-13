"""Smoke tests for the native panel-core HTTP surface (no sidecar).

Drives the real aiohttp dispatcher through the test fixture: health,
status, file roots, live state, auth (401), forbidden paths (403),
unknown routes (404) and the error JSON shape.
"""
import http.client
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parents[2]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from clawd_server import panel_core
from panel_core_http_fixture import start_panel_core_server, stop_panel_core_server


class PanelCoreSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = "smoke-test-key"
        # Self-contained file roots: other suites mutate FILE_ROOTS, so pin
        # a workspace root of our own instead of relying on the ambient one.
        cls.tmp = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.tmp.name) / "workspace"
        cls.workspace.mkdir()
        cls.loop, cls.runner, cls.thread, cls.port = start_panel_core_server(
            panel_core, cls.key
        )
        roots = {"workspace": {"path": cls.workspace, "label": "fixture",
                               "create": False, "writable": True}}
        cls._roots_patch = mock.patch.dict(panel_core.FILE_ROOTS, roots, clear=True)
        cls._roots_patch.start()

    @classmethod
    def tearDownClass(cls):
        stop_panel_core_server(cls.loop, cls.runner, cls.thread)
        cls._roots_patch.stop()
        cls.tmp.cleanup()

    def fetch(self, path, key="smoke-test-key", method="GET", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            headers = {"Content-Type": "application/json"}
            if key is not None:
                headers["X-Panel-Key"] = key
            conn.request(method, path,
                         json.dumps(body) if body is not None else None, headers)
            resp = conn.getresponse()
            raw = resp.read()
            try:
                data = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                data = None
            return resp.status, dict(resp.getheaders()), data
        finally:
            conn.close()

    def test_health_ok(self):
        status, _headers, data = self.fetch("/health")
        self.assertEqual(status, 200)
        self.assertEqual(data, {"ok": True})

    def test_status_returns_dict(self):
        status, _headers, data = self.fetch("/status")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)

    def test_file_roots_returns_dict(self):
        status, _headers, data = self.fetch("/files/roots")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)

    def test_live_state_returns_dict(self):
        status, _headers, data = self.fetch("/live/state")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)

    def test_missing_key_is_401_with_error_json(self):
        status, headers, data = self.fetch("/health", key=None)
        self.assertEqual(status, 401)
        self.assertEqual(data, {"error": {"code": "unauthorized", "message": "unauthorized"}})
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_wrong_key_is_401(self):
        status, _headers, data = self.fetch("/health", key="wrong-key")
        self.assertEqual(status, 401)
        self.assertEqual(data["error"]["code"], "unauthorized")

    def test_absolute_path_outside_roots_is_400(self):
        status, _headers, data = self.fetch(
            "/files/download?root=workspace&path=/etc/passwd")
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "invalid_path")

    def test_traversal_outside_root_is_403(self):
        status, _headers, data = self.fetch(
            "/files/download?root=workspace&path=../../etc/passwd")
        self.assertEqual(status, 403)
        self.assertEqual(data["error"]["code"], "outside_root")

    def test_unknown_route_is_404_with_error_json(self):
        status, headers, data = self.fetch("/no-such-route")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["code"], "not_found")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_error_json_shape(self):
        _status, _headers, data = self.fetch("/no-such-route")
        self.assertIsInstance(data, dict)
        self.assertIn("error", data)
        self.assertIsInstance(data["error"]["code"], str)
        self.assertIsInstance(data["error"]["message"], str)


if __name__ == "__main__":
    unittest.main()
