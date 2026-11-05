"""Sidecar robustness tests for request body parsing and error handling."""
import importlib
import io
import unittest


class SidecarRobustnessTests(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("sidecar.hermes_panel_sidecar")

    def _call_json_body(self, headers_dict, raw_body=b"{}"):
        class FakeHandler:
            headers = headers_dict
            rfile = io.BytesIO(raw_body)

        fake = FakeHandler()
        return self.mod.Handler._json_body(fake)

    def test_valid_content_length_parses_json(self):
        body = b'{"hello": "world"}'
        res = self._call_json_body({"Content-Length": str(len(body))}, body)
        self.assertEqual(res, {"hello": "world"})

    def test_missing_or_empty_content_length_defaults_empty_dict(self):
        res = self._call_json_body({}, b"")
        self.assertEqual(res, {})

    def test_non_numeric_content_length_raises_400(self):
        with self.assertRaises(self.mod.ApiError) as ctx:
            self._call_json_body({"Content-Length": "not-a-number"}, b"{}")
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, "invalid_content_length")

    def test_negative_content_length_raises_400(self):
        with self.assertRaises(self.mod.ApiError) as ctx:
            self._call_json_body({"Content-Length": "-10"}, b"{}")
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, "invalid_content_length")

    def test_oversized_content_length_raises_413(self):
        with self.assertRaises(self.mod.ApiError) as ctx:
            self._call_json_body({"Content-Length": str(self.mod.MAX_BODY_BYTES + 100)}, b"{}")
        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(ctx.exception.code, "too_large")

    def _handler(self, path="/status?token=secret-query"):
        from unittest.mock import Mock

        handler = object.__new__(self.mod.Handler)
        handler.headers = {"X-Panel-Key": "testkey"}
        handler.key = "testkey"
        handler.path = path
        handler.close_connection = False
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = io.BytesIO()
        return handler

    def test_route_connection_reset_returns_502_and_logs(self):
        import json
        from unittest.mock import Mock

        for error in (ConnectionResetError, BrokenPipeError):
            with self.subTest(error=error.__name__):
                handler = self._handler()
                handler._route = Mock(side_effect=error("upstream disconnected"))
                with self.assertLogs(self.mod.log, level="ERROR") as logs:
                    handler._dispatch("GET")
                handler.send_response.assert_called_once_with(502)
                self.assertEqual(json.loads(handler.wfile.getvalue()), {
                    "error": {"code": "upstream_reset", "message": "上游连接被重置，请稍后重试"},
                })
                self.assertIsNotNone(logs.records[0].exc_info)
                self.assertNotIn("secret-query", "\n".join(logs.output))

    def test_send_client_disconnect_is_silent(self):
        from unittest.mock import Mock

        for error in (BrokenPipeError, ConnectionResetError):
            with self.subTest(error=error.__name__):
                handler = self._handler()
                handler._route = Mock(return_value={"ok": True})
                handler.wfile = Mock()
                handler.wfile.write.side_effect = error("client disconnected")
                with self.assertNoLogs(self.mod.log):
                    handler._dispatch("GET")
                self.assertTrue(handler.close_connection)
                handler.send_response.assert_called_once_with(200)

    def test_download_stream_client_disconnect_is_silent(self):
        from contextlib import nullcontext
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        for error in (BrokenPipeError, ConnectionResetError):
            with self.subTest(error=error.__name__):
                handler = self._handler("/files/download?root=uploads&path=test.txt")
                handler.wfile = Mock()
                handler.wfile.write.side_effect = error("client disconnected")
                opened = (io.BytesIO(b"test"), SimpleNamespace(st_size=4), Path("test.txt"))
                with patch.object(self.mod, "_open_download", return_value=nullcontext(opened)), \
                     self.assertNoLogs(self.mod.log):
                    handler._dispatch("GET")
                handler.wfile.write.assert_called_once_with(b"test")
                self.assertTrue(handler.close_connection)
                handler.send_response.assert_called_once_with(200)

    def test_direct_client_io_paths_disconnect_silently(self):
        from unittest.mock import Mock

        for method, path, target in (
            ("GET", "/files/artifact", "_artifact"),
            ("GET", "/files/download", "_download"),
            ("POST", "/files/upload", "_upload"),
        ):
            for error in (BrokenPipeError, ConnectionResetError):
                with self.subTest(path=path, error=error.__name__):
                    handler = self._handler(path)
                    operation = Mock(side_effect=error("client disconnected"))
                    setattr(handler, target, operation)
                    with self.assertNoLogs(self.mod.log):
                        handler._dispatch(method)
                    operation.assert_called_once()
                    self.assertTrue(handler.close_connection)
                    handler.send_response.assert_not_called()

    def test_restart_service_timeout_expired_raises_504(self):
        from unittest.mock import patch
        import subprocess

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=120)

        with patch("subprocess.run", side_effect=fake_run):
            with self.assertRaises(self.mod.ApiError) as ctx:
                self.mod.restart_service("hermes-gateway", force=True)
            self.assertEqual(ctx.exception.status, 504)
            self.assertEqual(ctx.exception.code, "restart_timeout")

    def test_restart_activity_sqlite_failure_is_503_without_systemctl(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "state.db").touch()
            with patch.object(self.mod, "HERMES_HOME", home), \
                 patch.object(self.mod.sqlite3, "connect", side_effect=sqlite3.OperationalError("database is locked")), \
                 patch.object(self.mod.subprocess, "run") as restart:
                with self.assertRaises(self.mod.ApiError) as ctx:
                    self.mod.restart_service("hermes-gateway", force=False)
                self.assertEqual((ctx.exception.status, ctx.exception.code), (503, "activity_state_unavailable"))
                restart.assert_not_called()

    def test_restart_activity_io_failure_is_503_without_systemctl(self):
        from unittest.mock import patch
        with patch.object(self.mod.HERMES_HOME.__class__, "exists", side_effect=PermissionError("denied")), \
             patch.object(self.mod.subprocess, "run") as restart:
            with self.assertRaises(self.mod.ApiError) as ctx:
                self.mod.restart_service("hermes-gateway", force=False)
            self.assertEqual((ctx.exception.status, ctx.exception.code), (503, "activity_state_unavailable"))
            restart.assert_not_called()

    def test_restart_force_bypasses_unavailable_activity_query(self):
        from unittest.mock import patch, Mock
        with patch.object(self.mod, "_active_turn_count", side_effect=AssertionError("must bypass")), \
             patch.object(self.mod.subprocess, "run", return_value=Mock(returncode=0, stdout="", stderr="")) as restart:
            self.assertEqual(self.mod.restart_service("hermes-gateway", force=True), {"ok": True, "name": "hermes-gateway"})
            self.assertEqual(restart.call_args.kwargs["timeout"], 120)

    def test_restart_gateway_with_active_lease_raises_409_unless_forced(self):
        from unittest.mock import patch, MagicMock

        with patch.object(self.mod, "_active_turn_count", return_value=2):
            # 未指定 force 时应拦截并返回 409
            with self.assertRaises(self.mod.ApiError) as ctx:
                self.mod.restart_service("hermes-gateway", force=False)
            self.assertEqual(ctx.exception.status, 409)
            self.assertEqual(ctx.exception.code, "active_runs_exist")

            # 指定 force=True 时允许执行重启
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                res = self.mod.restart_service("hermes-gateway", force=True)
                self.assertEqual(res, {"ok": True, "name": "hermes-gateway"})


if __name__ == "__main__":
    unittest.main()
