"""Tests for backend and plugin audit remediation (F02, F03, F05, F06, F07, F08).
All tests run against hermes-panel-stream and use isolated sandbox resources only.
"""
import importlib.util
import os
from pathlib import Path
from typing import Any, cast
import unittest
from unittest.mock import Mock

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_PATH = REPO_ROOT / "plugins" / "browser-window-guard" / "__init__.py"


class AuditBackendSecurityTests(unittest.TestCase):
    def test_f08_browser_vault_list_allowed_and_writes_blocked(self):
        spec = importlib.util.spec_from_file_location("browser_guard_f08", GUARD_PATH)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        guard_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard_mod)

        ctx = Mock()
        guard_mod.register(ctx)
        pre_tool_call = None
        for call_args in ctx.register_hook.call_args_list:
            if call_args[0][0] == "pre_tool_call":
                pre_tool_call = call_args[0][1]
                break
        self.assertIsNotNone(pre_tool_call)
        assert callable(pre_tool_call)

        # Unified browser_vault action=list must be permitted (returns None)
        res_list = pre_tool_call(
            tool_name="browser_vault",
            args={"action": "list"},
            session_id="s1",
            turn_id="t1",
        )
        self.assertIsNone(res_list, "browser_vault action=list should not be blocked")

        # Mutating actions must remain blocked
        for write_action in ("fill", "save_login", "enter_code", "unlock", "delete", "unknown"):
            res_write = pre_tool_call(
                tool_name="browser_vault",
                args={"action": write_action},
                session_id="s1",
                turn_id="t1",
            )
            self.assertIsNotNone(res_write, f"browser_vault action={write_action} must remain blocked")
            assert isinstance(res_write, dict)
            self.assertEqual(res_write.get("action"), "block")

        # Missing or non-dict args must also remain blocked
        res_empty = pre_tool_call(
            tool_name="browser_vault",
            args={},
            session_id="s1",
            turn_id="t1",
        )
        self.assertIsNotNone(res_empty)
        assert isinstance(res_empty, dict)
        self.assertEqual(res_empty.get("action"), "block")

    def test_f06_current_model_base_url_redacted(self):
        from unittest.mock import patch
        import sys
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        clawd_root = Path(__file__).resolve().parents[3]
        if str(clawd_root) not in sys.path:
            sys.path.insert(0, str(clawd_root))
        from sidecar import hermes_panel_sidecar as sidecar_mod

        url = "https://audit-user:synthetic-password@example.invalid/v1"
        fake_config = {
            "model": {"provider": "custom_prov", "default": "model-x", "base_url": url},
            "providers": {"custom_prov": {"name": "Custom", "base_url": url, "models": ["model-x"]}},
            "model_catalog": {},
        }
        with patch.object(sidecar_mod, "config_get_json", side_effect=lambda k: fake_config.get(k)):
            res = sidecar_mod.get_models()

        current_url = res.get("current", {}).get("base_url")
        provider_url = res.get("providers", [{}])[0].get("baseUrl")
        self.assertNotIn("synthetic-password", current_url)
        self.assertEqual(current_url, provider_url, "current base_url must match provider baseUrl redaction")

    def test_f05_yaml_quoted_keys_redacted(self):
        import sys
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        clawd_root = Path(__file__).resolve().parents[3]
        if str(clawd_root) not in sys.path:
            sys.path.insert(0, str(clawd_root))
        from sidecar import hermes_panel_sidecar as sidecar_mod

        if sidecar_mod.yaml is None:
            self.skipTest("PyYAML not installed in current interpreter; test in venv")

        synthetic_yaml = (
            '"api_keys":\n'
            '  - opaque-example-credential\n'
            '"private_key": example-private-value\n'
            '\'nested_secret\':\n'
            '  sub_token: secret-sub-value\n'
            'display_title: normal_value\n'
        )
        redacted = sidecar_mod._redact_yaml(synthetic_yaml)
        self.assertNotIn("opaque-example-credential", redacted)
        self.assertNotIn("example-private-value", redacted)
        self.assertNotIn("secret-sub-value", redacted)
        self.assertIn("display_title: normal_value", redacted)

    def test_f05_yaml_structured_safety_and_conservative_rejection(self):
        import sys
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        clawd_root = Path(__file__).resolve().parents[3]
        if str(clawd_root) not in sys.path:
            sys.path.insert(0, str(clawd_root))
        from sidecar import hermes_panel_sidecar as sidecar_mod

        if sidecar_mod.yaml is None:
            self.skipTest("PyYAML not installed in current interpreter; test in venv")

        # 1. Flow mapping with secret key
        flow_map = 'env: {api_key: "flow-secret-token-12345", normal: 123}'
        redacted = sidecar_mod._redact_yaml(flow_map)
        self.assertNotIn("flow-secret-token-12345", redacted)

        # 2. Flow mapping in list
        flow_list = '- {secret: list-secret-token-54321, name: safe}'
        redacted_list = sidecar_mod._redact_yaml(flow_list)
        self.assertNotIn("list-secret-token-54321", redacted_list)

        # 3. Escape encoded sensitive key e.g. "api\x5fkey" or "api\u005fkey"
        escape_key = '"api\\x5fkeys":\n  - escaped-secret-val-999'
        redacted_esc = sidecar_mod._redact_yaml(escape_key)
        self.assertNotIn("escaped-secret-val-999", redacted_esc)

        # 4. Alias to secret (&anchor, *alias)
        alias_yaml = 'tokens: {api_key: &anchor alias-secret-val}\npublic_ref: *anchor'
        redacted_alias = sidecar_mod._redact_yaml(alias_yaml)
        self.assertNotIn("alias-secret-val", redacted_alias)

        # 5. Malformed YAML syntax must be conservatively rejected and NEVER return raw text
        malformed = '::: invalid ::: yaml {{[[\nunclosed_secret: raw-opaque-secret-should-not-leak\n'
        with self.assertRaises(sidecar_mod.ApiError) as cm:
            sidecar_mod._redact_yaml(malformed)
        err = cm.exception
        self.assertEqual(err.status, 500)
        self.assertNotIn("raw-opaque-secret-should-not-leak", str(err.message))
        self.assertNotIn("raw-opaque-secret-should-not-leak", getattr(err, "code", ""))

        # 6. get_config endpoint with malformed config raises ApiError without leaking raw secret in error response
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            cfg.write_text('bad_syntax: {{[[\nsuper_secret: leak_candidate_6789\n')
            with patch.object(sidecar_mod, "HERMES_HOME", Path(td)):
                with self.assertRaises(sidecar_mod.ApiError) as cm_cfg:
                    sidecar_mod.get_config()
                self.assertNotIn("leak_candidate_6789", str(cm_cfg.exception.message))

    def test_f05_quoted_comma_secret_is_never_partially_returned(self):
        from sidecar import hermes_panel_sidecar as sidecar_mod
        if sidecar_mod.yaml is None:
            self.skipTest("PyYAML not installed in current interpreter; test in venv")
        source = 'env: {api_key: "opaque-first,opaque-rest-secret", normal: 123}\n'
        try:
            result = sidecar_mod._redact_yaml(source)
        except sidecar_mod.ApiError as error:
            self.assertNotIn("opaque-rest-secret", str(error))
            self.assertEqual(error.code, "config_parse_failed")
        else:
            self.assertNotIn("opaque-first", result)
            self.assertNotIn("opaque-rest-secret", result)

    def test_f05_plain_scalar_apostrophe_is_not_an_unclosed_quote(self):
        from sidecar import hermes_panel_sidecar as sidecar_mod
        if sidecar_mod.yaml is None:
            self.skipTest("PyYAML not installed in current interpreter; test in venv")
        source = "display_title: user's panel\n"
        self.assertIn("user's panel", sidecar_mod._redact_yaml(source))

    def test_f07_long_unicode_filename_upload_succeeds(self):
        import io
        import tempfile
        from unittest.mock import patch
        import sys
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        clawd_root = Path(__file__).resolve().parents[3]
        if str(clawd_root) not in sys.path:
            sys.path.insert(0, str(clawd_root))
        from sidecar import hermes_panel_sidecar as sidecar_mod

        long_name = "文" * 80 + ".txt"
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            folder = home / "uploads"
            folder.mkdir()
            roots = {"uploads": {"path": folder, "label": "test", "writable": True, "create": True}}
            with patch.dict(os.environ, {"CLAWD_HOME": str(home)}), patch.object(sidecar_mod, "FILE_ROOTS", roots):
                h = object.__new__(sidecar_mod.Handler)
                setattr(h, "headers", {"Content-Length": "7"})
                setattr(h, "rfile", io.BytesIO(b"content"))
                res = h._upload({"name": long_name, "dir": "unicode_test"})
                dest_file = Path(res["absPath"])
                self.assertTrue(dest_file.is_file())
                self.assertEqual(dest_file.read_bytes(), b"content")
                self.assertEqual(dest_file.name, long_name)

    def test_f02_concurrent_same_name_upload_never_overwrites(self):
        import concurrent.futures
        import io
        import tempfile
        import threading
        from unittest.mock import patch
        import sys
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        clawd_root = Path(__file__).resolve().parents[3]
        if str(clawd_root) not in sys.path:
            sys.path.insert(0, str(clawd_root))
        from sidecar import hermes_panel_sidecar as sidecar_mod

        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            folder = home / "uploads"
            folder.mkdir()
            roots = {"uploads": {"path": folder, "label": "test", "writable": True, "create": True}}
            barrier = threading.Barrier(2)

            orig_unique = sidecar_mod._unique
            def select_name(p):
                selected = orig_unique(p)
                try:
                    barrier.wait(timeout=2)
                except threading.BrokenBarrierError:
                    pass
                return selected

            def do_upload(payload: bytes):
                h = object.__new__(sidecar_mod.Handler)
                setattr(h, "headers", {"Content-Length": str(len(payload))})
                setattr(h, "rfile", io.BytesIO(payload))
                with patch.dict(os.environ, {"CLAWD_HOME": str(home)}), patch.object(sidecar_mod, "FILE_ROOTS", roots), patch.object(sidecar_mod, "_unique", select_name):
                    return h._upload({"name": "same.txt", "dir": "concurrent_test"})

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(do_upload, b"AAA"), pool.submit(do_upload, b"BBB")]
                results = [f.result() for f in futures]

            dest_paths = [r["absPath"] for r in results]
            self.assertEqual(len(set(dest_paths)), 2, "Both uploads must have distinct paths")
            files = list((folder / "concurrent_test").iterdir())
            self.assertEqual(len(files), 2, "Both files must exist on disk")
            contents = {f.read_bytes() for f in files}
            self.assertEqual(contents, {b"AAA", b"BBB"})


if __name__ == "__main__":
    unittest.main()
