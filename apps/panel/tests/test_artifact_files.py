"""Artifact contract exercised through a real, isolated HTTP sidecar."""
import concurrent.futures
import hashlib
import http.client
import importlib
import json
import os
import socket
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import quote, urlencode


class ArtifactFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "clawd-home"
        self.home.mkdir()
        self.base = self.home / "workspace"
        self.base.mkdir()
        env = mock.patch.dict(os.environ, {"CLAWD_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.mod = importlib.import_module("sidecar.hermes_panel_sidecar")
        self.roots = {"workspace": {"path": self.base, "label": "fixture", "create": False, "writable": True}}
        roots_patch = mock.patch.dict(self.mod.FILE_ROOTS, self.roots, clear=True)
        roots_patch.start()
        self.addCleanup(roots_patch.stop)
        handler = type("FixtureHandler", (self.mod.Handler,), {"key": "artifact-test-key"})
        self.server = self.mod.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, *, preview=False, key: str | None = "artifact-test-key", endpoint="artifact", raw_query=None):
        params = {"path": str(path)}
        if endpoint == "download":
            params["root"] = "workspace"
        if preview:
            params["preview"] = "1"
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            conn.request("GET", f"/files/{endpoint}?{raw_query if raw_query is not None else urlencode(params)}",
                         headers={"X-Panel-Key": key} if key is not None else {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def assert_error(self, response, status, code=None):
        actual, headers, body = response
        self.assertEqual(actual, status, body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        error = json.loads(body)["error"]
        self.assertIsInstance(error["message"], str)
        if code is not None:
            self.assertEqual(error["code"], code)

    def test_dotfiles_and_credential_containers_under_root_are_denied(self):
        for rel in (".npmrc", ".git/config", ".aws/credentials", "proj/.env.production", "credentials/a.txt",
                    "Secrets/a.txt", "tokens/a.txt", "node_modules/x.js", "client.p12", "client.PFX"):
            path = self.base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"secret")
            with self.subTest(rel=rel):
                self.assert_error(self.request(path), 403, "denied")
        ok = self.base / "reports" / "credit.txt"
        ok.parent.mkdir()
        ok.write_bytes(b"fine")
        self.assertEqual(self.request(ok)[0], 200)

    def test_absolute_artifact_returns_exact_bytes_and_unicode_filename(self):
        path = self.base / "报告 空格+百分%井#问?.pdf"
        data = b"%PDF-1.7\n\x00binary\xff"
        path.write_bytes(data)
        status, headers, body = self.request(path)
        self.assertEqual(status, 200, body)
        self.assertEqual(body, data)
        self.assertEqual(headers["Content-Length"], str(len(data)))
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertEqual(headers["Content-Disposition"], f"attachment; filename*=UTF-8''{quote(path.name)}")
        self.assertEqual(set(self.mod.FILE_ROOTS), {"workspace"})

    def test_artifact_requires_authentication_before_path_access(self):
        path = self.base / "public.txt"
        path.write_bytes(b"not public")
        for key in (None, "wrong-key"):
            with self.subTest(key=key):
                self.assert_error(self.request(path, key=key), 401, "unauthorized")

    def test_lexical_path_rejects_relative_traversal_and_control_characters(self):
        (self.base / "public.txt").write_bytes(b"public")
        (self.base / "folder").mkdir()
        invalid = ["", "public.txt", "~/public.txt", "file:///public.txt", "//" + str(self.base).lstrip("/") + "/public.txt",
                   str(self.base) + "/folder/../public.txt", str(self.base) + "/./public.txt",
                   str(self.base) + "/public.txt\x00", str(self.base) + "/public\n.txt", str(self.base) + "/a\\b"]
        for path in invalid:
            with self.subTest(path=path):
                self.assert_error(self.request(path), 400, "invalid_path")

    def test_forbidden_original_segments_remain_denied_after_resolution(self):
        public = self.base / "public.txt"
        public.write_bytes(b"public")
        for name in (".env", ".env.local", "config.yaml", "config.yml", "state.db", "state.db-wal", "state.db-shm",
                     "secret.KEY", "cert.pem", "id_rsa", "authorized_keys", "auth.json"):
            link = self.base / name
            link.symlink_to(public)
            with self.subTest(name=name):
                self.assert_error(self.request(link), 403, "denied")
        folder = self.base / "config.yaml"
        folder.unlink()
        folder.mkdir()
        (folder / "report.txt").write_bytes(b"not allowed")
        self.assert_error(self.request(folder / "report.txt"), 403, "denied")

    def test_symlinks_are_rejected_even_when_the_destination_is_in_root(self):
        public = self.base / "public.txt"
        public.write_bytes(b"public")
        (self.base / "alias.txt").symlink_to(public)
        folder = self.base / "folder"
        folder.mkdir()
        (folder / "inside.txt").write_bytes(b"inside")
        (self.base / "linked-folder").symlink_to(folder, target_is_directory=True)
        for path in (self.base / "alias.txt", self.base / "linked-folder" / "inside.txt"):
            with self.subTest(path=path):
                self.assert_error(self.request(path), 403)

    def test_final_file_symlink_swap_after_validation_never_leaks_bytes(self):
        path = self.base / "report.txt"
        path.write_bytes(b"safe")
        secret = Path(self.tmp.name) / "outside-secret.txt"
        secret.write_bytes(b"MUST NOT LEAK")
        safe_path = self.mod._safe_path

        def swap_after_check(root_id, rel):
            result = safe_path(root_id, rel)
            path.unlink()
            path.symlink_to(secret)
            return result

        with mock.patch.object(self.mod, "_safe_path", side_effect=swap_after_check):
            response = self.request(path)
        self.assert_error(response, 403)
        self.assertNotIn(b"MUST NOT LEAK", response[2])

    def test_intermediate_directory_symlink_swap_after_validation_is_rejected(self):
        folder = self.base / "nested"
        folder.mkdir()
        (folder / "report.txt").write_bytes(b"safe")
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "report.txt").write_bytes(b"MUST NOT LEAK")
        safe_path = self.mod._safe_path

        def swap_after_check(root_id, rel):
            result = safe_path(root_id, rel)
            folder.rename(self.base / "old-nested")
            folder.symlink_to(outside, target_is_directory=True)
            return result

        with mock.patch.object(self.mod, "_safe_path", side_effect=swap_after_check):
            response = self.request(folder / "report.txt")
        self.assert_error(response, 403)
        self.assertNotIn(b"MUST NOT LEAK", response[2])

    def test_fifo_is_rejected_without_blocking(self):
        path = self.base / "pipe.txt"
        os.mkfifo(path)
        self.assert_error(self.request(path), 403, "unsafe_file")

    def test_missing_files_and_directories_return_structured_not_found(self):
        for path in (self.base / "missing.txt", self.base, self.base / "missing" / "child.txt"):
            with self.subTest(path=path):
                self.assert_error(self.request(path), 404, "not_found")

    def test_outside_roots_and_resolved_forbidden_targets_are_denied(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_bytes(b"outside")
        (self.base / "escape.txt").symlink_to(outside)
        secret = self.base / ".env"
        secret.write_bytes(b"secret")
        (self.base / "innocent.txt").symlink_to(secret)
        for path in (outside, Path(str(self.base) + "-sibling") / "report.txt", self.base / "escape.txt", self.base / "innocent.txt",
                     Path("/etc/passwd"), Path("/root/.ssh/id_rsa")):
            with self.subTest(path=path):
                self.assert_error(self.request(path), 403)

    def test_raster_preview_requires_matching_extension_and_signature(self):
        signatures = {
            "png": (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24, "image/png"),
            "jpg": (b"\xff\xd8\xff\xe0" + b"\x00" * 24, "image/jpeg"),
            "jpeg": (b"\xff\xd8\xff\xe1" + b"\x00" * 24, "image/jpeg"),
            "gif": (b"GIF89a\x01\x00\x01\x00" + b"\x00" * 24, "image/gif"),
            "webp": (b"RIFF\x18\x00\x00\x00WEBPVP8 " + b"\x00" * 24, "image/webp"),
            "bmp": (b"BM\x26\x00\x00\x00" + b"\x00" * 32, "image/bmp"),
        }
        for suffix, (data, content_type) in signatures.items():
            path = self.base / f"preview.{suffix.upper()}"
            path.write_bytes(data)
            with self.subTest(suffix=suffix):
                status, headers, body = self.request(path, preview=True)
                self.assertEqual(status, 200, body)
                self.assertEqual(body, data)
                self.assertEqual(headers["Content-Type"], content_type)
                self.assertTrue(headers["Content-Disposition"].startswith("inline;"))
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("sandbox", headers["Content-Security-Policy"])
        old_gif = self.base / "old.gif"
        old_gif.write_bytes(b"GIF87a\x01\x00\x01\x00" + b"\x00" * 24)
        self.assertTrue(self.request(old_gif, preview=True)[1]["Content-Disposition"].startswith("inline;"))
        for chunk in (b"VP8L", b"VP8X"):
            webp = self.base / "lossless.webp"
            webp.write_bytes(b"RIFF\x18\x00\x00\x00WEBP" + chunk + b"\x00" * 24)
            self.assertTrue(self.request(webp, preview=True)[1]["Content-Disposition"].startswith("inline;"))

    def test_fake_or_mismatched_raster_is_downloaded_as_opaque_bytes(self):
        for suffix, data in (("png", b"<html><script>alert(1)</script>"), ("jpg", b"GIF89a"),
                             ("jpeg", b"\xff\xd8"), ("gif", b"GIF00a"), ("webp", b"RIFF\x00\x00\x00\x00AVI VP8 "),
                             ("bmp", b"BM"), ("png", b"")):
            path = self.base / f"fake.{suffix}"
            path.write_bytes(data)
            with self.subTest(suffix=suffix, data=data):
                status, headers, body = self.request(path, preview=True)
                self.assertEqual(status, 200, body)
                self.assertEqual(body, data)
                self.assertEqual(headers["Content-Type"], "application/octet-stream")
                self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))

    def test_active_formats_and_non_raster_are_always_sandboxed_downloads(self):
        for suffix, data in (("svg", b'<svg onload="alert(1)"></svg>'), ("html", b"<script>alert(1)</script>"),
                             ("htm", b"<html>hello</html>"), ("pdf", b"%PDF-1.7"), ("txt", b"GIF89a"), ("avif", b"unsupported")):
            path = self.base / f"report.{suffix}"
            path.write_bytes(data)
            for preview in (False, True):
                with self.subTest(suffix=suffix, preview=preview):
                    status, headers, body = self.request(path, preview=preview)
                    self.assertEqual(status, 200, body)
                    self.assertEqual(body, data)
                    self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
                    self.assertIn("sandbox", headers["Content-Security-Policy"])
                    self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                    self.assertEqual(headers["Cache-Control"], "no-store")

    def test_existing_download_contract_still_uses_root_and_relative_path(self):
        path = self.base / "原有+下载.png"
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
        path.write_bytes(data)
        status, headers, body = self.request(path.name, endpoint="download", preview=True)
        self.assertEqual(status, 200, body)
        self.assertEqual(body, data)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Content-Disposition"], f"attachment; filename*=UTF-8''{quote(path.name)}")
        self.assert_error(self.request(path.name, endpoint="download", key=None), 401, "unauthorized")
        self.assert_error(self.request("missing.txt", endpoint="download"), 404, "not_found")

    def test_unexpected_errors_do_not_log_query_credentials_or_authentication(self):
        raw = urlencode({"path": str(self.base / "file.txt"), "api_key": "query-credential-MUST-NOT-LOG"})
        with mock.patch.object(self.mod.Handler, "_artifact", side_effect=RuntimeError("fixture failure")):
            with self.assertLogs(self.mod.log, level="ERROR") as captured:
                response = self.request("ignored", raw_query=raw)
        self.assert_error(response, 500, "internal")
        output = "\n".join(captured.output)
        self.assertNotIn("query-credential-MUST-NOT-LOG", output)
        self.assertNotIn("artifact-test-key", output)
        self.assertNotIn(str(self.base), output)

    def test_stream_stops_at_descriptor_size_if_file_grows_after_fstat(self):
        path = self.base / "growing.txt"
        path.write_bytes(b"original")
        fstat = self.mod.os.fstat

        def grow_after_fstat(fd):
            info = fstat(fd)
            with path.open("ab") as writer:
                writer.write(b"EXTRA BYTES MUST NOT BE SENT")
            return info

        with mock.patch.object(self.mod.os, "fstat", side_effect=grow_after_fstat):
            with socket.create_connection(self.server.server_address, timeout=5) as client:
                url = "/files/artifact?" + urlencode({"path": str(path)})
                client.sendall(f"GET {url} HTTP/1.0\r\nX-Panel-Key: artifact-test-key\r\n\r\n".encode("ascii"))
                chunks = []
                while chunk := client.recv(65536):
                    chunks.append(chunk)
        header, body = b"".join(chunks).split(b"\r\n\r\n", 1)
        self.assertIn(b"Content-Length: 8\r\n", header + b"\r\n")
        self.assertEqual(body, b"original")

    def test_concurrent_large_downloads_stream_in_bounded_reads(self):
        path = self.base / "large.bin"
        block = bytes(range(256)) * 256
        expected_hash = hashlib.sha256()
        with path.open("wb") as writer:
            for _ in range(128):
                writer.write(block)
                expected_hash.update(block)
        workers = 4
        barrier = threading.Barrier(workers, timeout=5)
        read_sizes = []
        active = 0
        peak_active = 0
        lock = threading.Lock()
        fdopen = self.mod.os.fdopen

        class TrackingReader:
            def __init__(self, fd, *args, **kwargs):
                self.stream = fdopen(fd, *args, **kwargs)
                self.first = True

            def __enter__(self):
                nonlocal active, peak_active
                with lock:
                    active += 1
                    peak_active = max(peak_active, active)
                return self

            def __exit__(self, *args):
                nonlocal active
                try:
                    self.stream.close()
                finally:
                    with lock:
                        active -= 1

            def read(self, size=-1):
                with lock:
                    read_sizes.append(size)
                if self.first:
                    self.first = False
                    barrier.wait()
                return self.stream.read(size)

        def consume(_):
            conn = http.client.HTTPConnection(*self.server.server_address, timeout=10)
            try:
                conn.request("GET", "/files/artifact?" + urlencode({"path": str(path)}),
                             headers={"X-Panel-Key": "artifact-test-key"})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(int(response.getheader("Content-Length") or "-1"), path.stat().st_size)
                digest = hashlib.sha256()
                count = 0
                while chunk := response.read(65536):
                    digest.update(chunk)
                    count += len(chunk)
                return digest.hexdigest(), count
            finally:
                conn.close()

        with mock.patch.object(self.mod.os, "fdopen", side_effect=TrackingReader):
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(consume, range(workers)))
        self.assertEqual(results, [(expected_hash.hexdigest(), path.stat().st_size)] * workers)
        self.assertEqual(peak_active, workers)
        self.assertTrue(read_sizes)
        self.assertTrue(all(0 < size <= 65536 for size in read_sizes), read_sizes)

    def test_current_allowlists_are_used_without_fallback_or_root_expansion(self):
        snapshot = dict(self.mod.FILE_ROOTS)
        created = {}
        for root_id in ("uploads", "cron", "images"):
            base = self.home / ("cron/output" if root_id == "cron" else root_id)
            base.mkdir(parents=True)
            created[root_id] = base
            self.mod.FILE_ROOTS[root_id] = {"path": base, "label": root_id, "create": False, "writable": False}
            path = base / "artifact.bin"
            path.write_bytes(root_id.encode())
            self.assertEqual(self.request(path)[2], root_id.encode())
        for root_id in ("uploads", "cron", "images"):
            del self.mod.FILE_ROOTS[root_id]
        self.assertEqual(self.mod.FILE_ROOTS, snapshot)
        self.assert_error(self.request(created["images"] / "artifact.bin"), 403, "outside_root")

    def test_symlink_loop_and_symlinked_allowlist_root_fail_closed(self):
        loop = self.base / "loop.txt"
        loop.symlink_to(loop)
        self.assert_error(self.request(loop), 403)
        alias_root = self.home / "root-alias"
        alias_root.symlink_to(self.base, target_is_directory=True)
        self.mod.FILE_ROOTS["workspace"]["path"] = alias_root
        (self.base / "public.txt").write_bytes(b"public")
        self.assert_error(self.request(alias_root / "public.txt"), 403)

    def test_percent_encoded_paths_are_decoded_once_not_recursively(self):
        encoded_name = self.base / "%2e%2e%2fpublic.txt"
        encoded_name.write_bytes(b"literal percent filename")
        self.assertEqual(self.request(encoded_name)[2], b"literal percent filename")
        raw = "path=" + quote(str(self.base), safe="") + "%2Ffolder%2F%2e%2e%2Fpublic.txt"
        self.assert_error(self.request("ignored", raw_query=raw), 400, "invalid_path")

    def test_preview_flag_is_explicit_and_download_is_default(self):
        path = self.base / "valid.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
        for flag in (None, "", "true", "0", "2"):
            params = {"path": str(path)}
            if flag is not None:
                params["preview"] = flag
            self.assertTrue(self.request(path, raw_query=urlencode(params))[1]["Content-Disposition"].startswith("attachment;"))

    def test_non_ascii_authentication_is_unauthorized_not_internal_error(self):
        self.assert_error(self.request(self.base / "report.txt", key="é"), 401, "unauthorized")

    def test_stream_read_failure_closes_response_without_appending_json(self):
        path = self.base / "unreadable.txt"
        path.write_bytes(b"file bytes")
        fdopen = self.mod.os.fdopen

        class FailingReader:
            def __init__(self, fd, *args, **kwargs):
                self.stream = fdopen(fd, *args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def read(self, size):
                raise OSError("injected read failure")

        with mock.patch.object(self.mod.os, "fdopen", side_effect=FailingReader):
            with mock.patch.object(self.mod.log, "exception") as logged:
                with socket.create_connection(self.server.server_address, timeout=5) as client:
                    url = "/files/artifact?" + urlencode({"path": str(path)})
                    client.sendall(f"GET {url} HTTP/1.0\r\nX-Panel-Key: artifact-test-key\r\n\r\n".encode("ascii"))
                    chunks = []
                    while chunk := client.recv(65536):
                        chunks.append(chunk)
        response = b"".join(chunks)
        self.assertEqual(response.count(b"HTTP/1.0"), 1, response)
        self.assertEqual(response.split(b"\r\n\r\n", 1)[1], b"")
        logged.assert_called_once_with("file response stream failed")


if __name__ == "__main__":
    unittest.main()
