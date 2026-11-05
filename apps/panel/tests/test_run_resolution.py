"""Exact admission recovery over the real sidecar HTTP handler and synthetic SQLite."""
import json
import sqlite3
import unittest
from urllib.parse import urlencode
from unittest.mock import patch
import tests.test_session_run as fixture


class RunResolutionTests(fixture.SessionRunTests):
    def resolution_path(self, key="request-key-original"):
        return "/sessions/run/resolve?" + urlencode({"session_id": self.session_id, "idempotency_key": key})

    def keyed_run(self, key="request-key-original", **kwargs):
        run = self.add_run(**kwargs)
        with self.connect(self.run_db) as conn:
            conn.execute("UPDATE run_idempotency SET idempotency_key=? WHERE run_id=?", (key, run["run_id"]))
        return run

    def test_resolution_exact_key_not_latest_and_read_only(self):
        run = self.keyed_run(created_at=self.now - 10, updated_at=self.now - 9)
        self.keyed_run("other-request", status="completed", created_at=self.now - 3)
        self.assert_response({"run": run, "busy": False,
                              "resolution": {"idempotency_key": "request-key-original", "state": "found"}},
                             path=self.resolution_path())

    def test_resolution_missing_is_not_a_terminal_status(self):
        self.keyed_run("another-key")
        self.assert_response({"run": None, "busy": False,
                              "resolution": {"idempotency_key": "request-key-original", "state": "not_found"}},
                             path=self.resolution_path())

    def test_resolution_absent_index_is_unavailable(self):
        self.assert_error(503, "session_run_unavailable", path=self.resolution_path())

    def test_resolution_wrong_session_ambiguous_scope_and_expiry_rejected(self):
        run = self.keyed_run(session_id="another_session")
        self.assert_error(409, "session_mismatch", path=self.resolution_path())
        with self.connect(self.run_db) as conn:
            raw = conn.execute("SELECT status_json FROM run_idempotency WHERE run_id=?", (run["run_id"],)).fetchone()[0]
            public = json.loads(raw)
            public["session_id"] = self.session_id
            conn.execute("UPDATE run_idempotency SET status_json=?", (json.dumps(public),))
        second = self.keyed_run("second-key")
        with self.connect(self.run_db) as conn:
            conn.execute("UPDATE run_idempotency SET scope='other-scope', idempotency_key='request-key-original' WHERE run_id=?", (second["run_id"],))
        self.assert_error(503, "session_run_unavailable", path=self.resolution_path())
        with self.connect(self.run_db) as conn:
            conn.execute("DELETE FROM run_idempotency WHERE run_id=?", (second["run_id"],))
            conn.execute("UPDATE run_idempotency SET retention_until=?", (self.now - 1,))
        with patch.object(self.module.time, "time", return_value=self.now):
            self.assert_error(503, "session_run_unavailable", path=self.resolution_path())

    def test_resolution_validation_auth_and_corruption_fail_closed(self):
        for key in ("", "bad key", "x" * 201, "abc\n", "../key"):
            with self.subTest(key=key):
                self.assert_error(400, "invalid_idempotency_key", path=self.resolution_path(key))
        self.assert_error(401, "unauthorized", path=self.resolution_path(), key="wrong")
        self.assert_error(404, "not_found", path=self.resolution_path(), method="POST")
        self.keyed_run()
        with self.connect(self.run_db) as conn:
            conn.execute("UPDATE run_idempotency SET status_json='not-json-private-value'")
        payload = self.assert_error(503, "session_run_unavailable", path=self.resolution_path())
        self.assertNotIn("private-value", json.dumps(payload))


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(RunResolutionTests(name) for name in loader.getTestCaseNames(RunResolutionTests)
                              if name.startswith("test_resolution_"))


if __name__ == "__main__":
    unittest.main()
