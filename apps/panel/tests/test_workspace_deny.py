"""Workspace browse must hide and refuse .git and hermes-panel-state.

Exercises list_dir and the /files download entry. Does not touch the real
workspace or the artifact dot-directory policy.
"""
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

import http.client
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlencode


class WorkspaceBrowseDenyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "clawd-home"
        self.home.mkdir()
        self.workspace = self.home / "workspace"
        self.uploads = self.home / "uploads"
        self.workspace.mkdir()
        self.uploads.mkdir()
        env = mock.patch.dict(os.environ, {"CLAWD_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

        git_config = self.workspace / ".git" / "config"
        git_config.parent.mkdir()
        git_config.write_bytes(b"git-secret-must-not-leak")
        state_db = self.workspace / "hermes-panel-state" / "live-tasks.sqlite3"
        state_db.parent.mkdir()
        state_db.write_bytes(b"state-secret-must-not-leak")
        (self.workspace / "notes.txt").write_bytes(b"notes-ok")
        (self.workspace / ".gitignore").write_bytes(b"gitignore-ok")
        (self.workspace / "data.sqlite3").write_bytes(b"sqlite-ok")
        nested = self.workspace / "nested"
        nested.mkdir()
        (nested / "keep.txt").write_bytes(b"keep-ok")
        (nested / "via-git").symlink_to(Path("..") / ".git", target_is_directory=True)

        (self.workspace / "link-git").symlink_to(Path(".git"), target_is_directory=True)
        (self.workspace / "link-state").symlink_to(Path("hermes-panel-state"), target_is_directory=True)
        (self.workspace / "link-git-config").symlink_to(Path(".git") / "config")
        (self.workspace / "link-state-db").symlink_to(Path("hermes-panel-state") / "live-tasks.sqlite3")
        (self.workspace / "link-notes").symlink_to(Path("notes.txt"))
        (self.uploads / "photo.txt").write_bytes(b"upload-ok")

        self.mod = panel_core
        roots = {
            "workspace": {"path": self.workspace, "label": "fixture", "writable": True, "create": False},
            "uploads": {"path": self.uploads, "label": "uploads", "writable": True, "create": False},
        }
        roots_patch = mock.patch.dict(self.mod.FILE_ROOTS, roots, clear=True)
        roots_patch.start()
        self.addCleanup(roots_patch.stop)
        self.loop, self.runner, self.thread, self.port = start_panel_core_server(
            self.mod, "deny-test-key"
        )
        self.server_address = ("127.0.0.1", self.port)
        self.addCleanup(self.stop_server)

    def stop_server(self):
        stop_panel_core_server(self.loop, self.runner, self.thread)

    def request(self, path):
        conn = http.client.HTTPConnection(*self.server_address, timeout=5)
        try:
            conn.request("GET", path, headers={"X-Panel-Key": "deny-test-key"})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def files(self, root, rel=""):
        return self.request("/files?" + urlencode({"root": root, "path": rel}))

    def download(self, root, rel):
        return self.request("/files/download?" + urlencode({"root": root, "path": rel}))

    def post(self, path, body=b"", headers=None):
        conn = http.client.HTTPConnection(*self.server_address, timeout=5)
        try:
            merged = {"X-Panel-Key": "deny-test-key"}
            if headers:
                merged.update(headers)
            conn.request("POST", path, body=body, headers=merged)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def assert_denied(self, response):
        status, body = response
        self.assertEqual(status, 403, body)
        self.assertNotIn(b"git-secret-must-not-leak", body)
        self.assertNotIn(b"state-secret-must-not-leak", body)
        self.assertEqual(json.loads(body)["error"]["code"], "denied")

    def assert_list_denied(self, rel):
        with self.assertRaises(self.mod.ApiError) as caught:
            self.mod.list_dir("workspace", rel)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(caught.exception.code, "denied")
        self.assert_denied(self.files("workspace", rel))

    def test_listing_hides_git_panel_state_and_links_but_keeps_abs_path(self):
        listed = self.mod.list_dir("workspace", "")
        names = {entry["name"] for entry in listed["entries"]}
        self.assertEqual(listed["absPath"], str(self.workspace))
        self.assertNotIn(".git", names)
        self.assertNotIn("hermes-panel-state", names)
        self.assertNotIn("link-git", names)
        self.assertNotIn("link-state", names)
        self.assertNotIn("link-git-config", names)
        self.assertNotIn("link-state-db", names)
        self.assertIn("notes.txt", names)
        self.assertIn(".gitignore", names)
        self.assertIn("data.sqlite3", names)
        self.assertIn("link-notes", names)
        self.assertIn("nested", names)

        status, body = self.files("workspace", "")
        self.assertEqual(status, 200, body)
        payload = json.loads(body)
        self.assertEqual(payload["absPath"], str(self.workspace))
        http_names = {entry["name"] for entry in payload["entries"]}
        self.assertNotIn(".git", http_names)
        self.assertNotIn("hermes-panel-state", http_names)
        self.assertNotIn("link-git", http_names)
        self.assertIn("notes.txt", http_names)

        nested = self.mod.list_dir("workspace", "nested")
        nested_names = {entry["name"] for entry in nested["entries"]}
        self.assertIn("keep.txt", nested_names)
        self.assertNotIn("via-git", nested_names)
        parent = self.mod.list_dir("workspace", "nested/..")
        parent_names = {entry["name"] for entry in parent["entries"]}
        self.assertNotIn(".git", parent_names)
        self.assertNotIn("hermes-panel-state", parent_names)
        self.assertIn("notes.txt", parent_names)

    def test_listing_denied_directories_via_dotdot_or_symlink_is_forbidden(self):
        for rel in (
            ".git",
            "hermes-panel-state",
            "nested/../.git",
            "nested/../hermes-panel-state",
            "link-git",
            "link-state",
            "nested/../link-git",
            "nested/via-git",
        ):
            with self.subTest(rel=rel):
                self.assert_list_denied(rel)

    def test_download_of_git_and_panel_state_is_forbidden(self):
        for rel in (
            ".git/config",
            "hermes-panel-state/live-tasks.sqlite3",
            "nested/../.git/config",
            "nested/../hermes-panel-state/live-tasks.sqlite3",
            "link-git/config",
            "link-state/live-tasks.sqlite3",
            "nested/../link-git/config",
            "link-git-config",
            "link-state-db",
        ):
            with self.subTest(rel=rel):
                self.assert_denied(self.download("workspace", rel))

    def test_ordinary_workspace_and_upload_files_still_list_and_download(self):
        for root, rel, payload in (
            ("workspace", "notes.txt", b"notes-ok"),
            ("workspace", ".gitignore", b"gitignore-ok"),
            ("workspace", "data.sqlite3", b"sqlite-ok"),
            ("uploads", "photo.txt", b"upload-ok"),
        ):
            with self.subTest(root=root, rel=rel):
                status, body = self.download(root, rel)
                self.assertEqual(status, 200, body)
                self.assertEqual(body, payload)
        status, body = self.files("uploads", "")
        self.assertEqual(status, 200, body)
        upload_names = {entry["name"] for entry in json.loads(body)["entries"]}
        self.assertEqual(upload_names, {"photo.txt"})

        status, body = self.download("workspace", "nested/../notes.txt")
        self.assertEqual(status, 400, body)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_path")
        status, body = self.download("workspace", "link-notes")
        self.assertEqual(status, 403, body)
        self.assertEqual(json.loads(body)["error"]["code"], "unsafe_file")
        self.assertNotIn(b"notes-ok", body)

    def test_artifact_upload_and_delete_cannot_touch_panel_state(self):
        state = self.workspace / "hermes-panel-state" / "live-tasks.sqlite3"
        status, body = self.request("/files/artifact?" + urlencode({"path": str(state)}))
        self.assert_denied((status, body))
        status, body = self.post(
            "/files/delete",
            json.dumps({"root": "workspace", "path": "hermes-panel-state/live-tasks.sqlite3"}).encode(),
            {"Content-Type": "application/json"},
        )
        self.assert_denied((status, body))
        self.assertTrue(state.is_file())
        self.assertEqual(state.read_bytes(), b"state-secret-must-not-leak")
        status, body = self.post(
            "/files/upload?" + urlencode({"root": "workspace", "dir": "hermes-panel-state", "name": "extra.txt"}),
            b"uploaded-secret",
            {"Content-Type": "application/octet-stream"},
        )
        self.assert_denied((status, body))
        self.assertFalse((self.workspace / "hermes-panel-state" / "extra.txt").exists())
