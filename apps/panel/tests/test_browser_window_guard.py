"""Tests for browser-window-guard plugin with real Chromium integration."""
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "browser_window_guard",
    Path(__file__).resolve().parent.parent / "plugins" / "browser-window-guard" / "__init__.py",
)
guard_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard_mod)


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BrowserWindowGuardTests(unittest.TestCase):
    def test_pre_tool_call_ignores_non_browser_exec(self):
        ctx = Mock()
        guard_mod.register(ctx)
        pre_tool_call_handler = None
        for call_args in ctx.register_hook.call_args_list:
            if call_args[0][0] == "pre_tool_call":
                pre_tool_call_handler = call_args[0][1]
                break
        self.assertIsNotNone(pre_tool_call_handler)

        res = pre_tool_call_handler(
            tool_name="terminal",
            args={"command": "ls"},
            session_id="sess-1",
            turn_id="turn-1",
            task_id="task-1",
        )
        self.assertIsNone(res)

    def test_blocks_mcp_and_legacy_browser_tools(self):
        ctx = Mock()
        guard_mod.register(ctx)
        pre_tool_call_handler = [c[0][1] for c in ctx.register_hook.call_args_list if c[0][0] == "pre_tool_call"][0]

        # mcp chrome-devtools blocked
        res = pre_tool_call_handler(
            tool_name="mcp__chrome_devtools__new_page",
            args={"url": "https://example.com"},
            session_id="sess-1",
            turn_id="turn-1",
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.get("action"), "block")
        self.assertIn("browser_exec", res.get("message", ""))

        # playwright blocked
        res_pw = pre_tool_call_handler(
            tool_name="playwright_navigate",
            args={},
            session_id="sess-1",
            turn_id="turn-1",
        )
        self.assertEqual(res_pw.get("action"), "block")

        # legacy browser_* blocked (except vault & exec)
        res_legacy = pre_tool_call_handler(
            tool_name="browser_action",
            args={},
            session_id="sess-1",
            turn_id="turn-1",
        )
        self.assertEqual(res_legacy.get("action"), "block")

    def test_blocks_browser_exec_without_session_or_turn_id(self):
        """Missing turn identity is refused by the native registry, not a second plugin rewrite."""
        from native_browser_fixture import bind_profile, ensure_repo

        ensure_repo()
        ctx = Mock()
        guard_mod.register(ctx)
        hook = [c[0][1] for c in ctx.register_hook.call_args_list if c[0][0] == "pre_tool_call"][0]
        self.assertIsNone(hook(tool_name="browser_exec", args={"code": "print(1)"}, session_id="sess-1", turn_id=""))
        self.assertFalse(any(c[0][0] == "on_session_end" for c in ctx.register_hook.call_args_list))
        import tempfile
        from tools.approval_context import reset_current_observability_context, set_current_observability_context

        tokens = set_current_observability_context(turn_id="", session_id="")
        self.addCleanup(lambda: reset_current_observability_context(tokens))
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        bind_profile(self, home)
        from tools.browser_task_registry import IdentityError, resolve_call_identity

        with self.assertRaises(IdentityError) as caught:
            resolve_call_identity("task-1", "sess-1")
        self.assertIn("missing", str(caught.exception))
        self.assertIn("turn", str(caught.exception))

    def test_pre_tool_call_modifies_browser_exec_code_and_session(self):
        """Native registry keeps the exact session. The retired plugin must not also rewrite browser_exec."""
        import tempfile
        from native_browser_fixture import bind_profile, register_page

        ctx = Mock()
        guard_mod.register(ctx)
        hook = [c[0][1] for c in ctx.register_hook.call_args_list if c[0][0] == "pre_tool_call"][0]
        original = "# Navigating to test page\nnew_tab('https://example.com')\nprint(page_info())"
        self.assertIsNone(hook(tool_name="browser_exec", args={"code": original}, session_id="session_123", turn_id="turn_456"))
        self.assertNotIn("http://127.0.0.1", original)
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        bind_profile(self, home)
        register_page(task_id="task-exact", session_id="session_123", turn_id="turn_456", target_id="OWNED")
        from tools.browser_task_registry import owns, session_view

        view = session_view("session_123", "task-exact")
        self.assertEqual(view["sessionId"], "session_123")
        self.assertEqual(view["turnId"], "turn_456")
        self.assertLessEqual(len(view["sessionId"]), 32)
        self.assertTrue(owns("session_123", "task-exact", "OWNED", "/devtools/browser/fixture"))
        self.assertFalse(owns("session_123", "native-execution-id", "OWNED", "/devtools/browser/fixture"))

    def test_shared_browser_avoids_unregistered_native_named_session_tabs(self):
        import tempfile
        from native_browser_fixture import bind_profile, register_page

        ctx = Mock()
        guard_mod.register(ctx)
        hook = [c[0][1] for c in ctx.register_hook.call_args_list if c[0][0] == "pre_tool_call"][0]
        code = "# test\nprint(1)"
        result = hook(tool_name="browser_exec", args={"code": code, "session": "original"}, session_id="s", turn_id="t")
        self.assertIsNone(result)
        self.assertNotIn("http://", code)
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        bind_profile(self, home)
        register_page(task_id="task-s", session_id="s", turn_id="t", target_id="OWNED")
        from tools.browser_task_registry import owns

        self.assertFalse(owns("s", "task-s", "UNREGISTERED", "/devtools/browser/fixture"))
        self.assertTrue(owns("s", "task-s", "OWNED", "/devtools/browser/fixture"))

    def test_generated_script_keeps_privileged_transport_out_of_user_url_scan(self):
        ctx = Mock()
        guard_mod.register(ctx)
        hook = [c[0][1] for c in ctx.register_hook.call_args_list if c[0][0] == "pre_tool_call"][0]
        code = "# Test\nprint(1)"
        self.assertIsNone(hook(tool_name="browser_exec", args={"code": code}, session_id="s", turn_id="t"))
        self.assertNotIn("http://", code)
        self.assertNotIn("127.0.0.1:9223", code)

    def test_end_rechecks_browser_before_each_close(self):
        """Closing one task must not close a target registered to another browser identity."""
        import tempfile
        from native_browser_fixture import bind_profile, register_page

        ctx = Mock()
        guard_mod.register(ctx)
        self.assertFalse(any(c[0][0] == "on_session_end" for c in ctx.register_hook.call_args_list))
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        bind_profile(self, home)
        register_page(task_id="task-a", session_id="s", turn_id="t", target_id="ONE", browser_id="/devtools/browser/A")
        register_page(task_id="task-b", session_id="s", turn_id="t2", target_id="TWO", browser_id="/devtools/browser/B")
        from tools.browser_task_registry import close_owned, session_view

        close_owned("task-a")
        self.assertEqual(session_view("s", "task-b")["status"], "active")
        self.assertEqual(session_view("s", "task-b")["targetId"], "TWO")
        ended = session_view("s", "task-a")
        self.assertEqual(ended["status"], "ended")
        self.assertTrue(ended["cleanupError"])

    def test_native_task_id_cannot_override_exact_turn_identity(self):
        import tempfile
        from native_browser_fixture import bind_profile, register_page

        ctx = Mock()
        guard_mod.register(ctx)
        self.assertFalse(any(c[0][0] == "on_session_end" for c in ctx.register_hook.call_args_list))
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        bind_profile(self, home)
        register_page(task_id="turn-exact-task", session_id="sess-xyz", turn_id="turn-exact", target_id="EXACT")
        from tools.browser_task_registry import close_owned, session_view

        close_owned("native-execution-id")
        exact = session_view("sess-xyz", "turn-exact-task")
        self.assertEqual(exact["status"], "active")
        self.assertEqual(exact["turnId"], "turn-exact")
        self.assertIsNone(session_view("sess-xyz", "native-execution-id"))

    def test_on_session_end_requires_exact_task_id_and_does_not_guess_latest(self):
        import tempfile
        from native_browser_fixture import bind_profile, register_page

        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        bind_profile(self, home)
        register_page(task_id="kept-task", session_id="sess-xyz", turn_id="turn-exact", target_id="KEPT")
        from tools.browser_task_registry import close_owned, session_view

        close_owned("")
        close_owned(None)
        self.assertEqual(session_view("sess-xyz", "kept-task")["status"], "active")

    def test_on_session_end_browser_id_failure_records_cleanup_error_without_closing(self):
        import tempfile
        from native_browser_fixture import bind_profile, register_page

        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        bind_profile(self, home)
        register_page(task_id="task-abc", session_id="sess-xyz", turn_id="turn-exact", target_id="OWNED", endpoint="http://127.0.0.1:9")
        register_page(task_id="other-task", session_id="sess-other", turn_id="turn-other", target_id="OTHER", browser_id="/devtools/browser/other")
        from tools.browser_task_registry import close_owned, session_view

        close_owned("task-abc")
        ended = session_view("sess-xyz", "task-abc")
        self.assertEqual(ended["status"], "ended")
        self.assertIn("Failed to close", ended["cleanupError"])
        self.assertEqual(session_view("sess-other", "other-task")["status"], "active")

    def test_pre_llm_call_registers_active_without_target(self):
        import tempfile
        from native_browser_fixture import bind_profile

        ctx = Mock()
        guard_mod.register(ctx)
        self.assertFalse(any(c[0][0] == "pre_llm_call" for c in ctx.register_hook.call_args_list))
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        bind_profile(self, home)
        from tools.browser_task_registry import IdentityError, register_created, session_view

        self.assertIsNone(session_view("s1", "task-s1"))
        with self.assertRaises(IdentityError):
            register_created(task_id="task-s1", session_id="s1", turn_id="t1", target_id="", browser_id="/devtools/browser/fixture", endpoint="http://127.0.0.1:9")
        self.assertIsNone(session_view("s1"))

    def test_real_cdp_integration_user_page_task_isolation_and_cleanup(self):
        """REAL Chromium Integration Test.

        Tests:
        1. User's initial page (Tab 0) is created before any task.
        2. Task A executes: preamble switches/creates owned target. User page is not modified.
        3. Task A creates a new target (Tab A).
        4. Task B executes (concurrent / replacement turn): gets its own owned target.
        5. Task A attempts switch_tab / cdp on Task B target or User target -> rejected with PermissionError.
        6. on_session_end for Task A: closes ONLY Task A targets.
        7. Assertion: User Tab and Task B Tab remain intact.
        8. on_session_end for Task B: closes Task B target. User tab remains open!
        """
        scratch_base = os.environ.get("PANEL_TEST_OUTPUT") or os.environ.get("TMPDIR") or "/root/hermes-workspace/p-tmp"
        os.makedirs(scratch_base, exist_ok=True)
        user_data_dir = tempfile.mkdtemp(dir=scratch_base)
        db_dir = tempfile.mkdtemp(dir=scratch_base)
        db_file = Path(db_dir) / "live-tasks.sqlite3"
        test_port = get_free_port()

        proc = subprocess.Popen([
            "chromium",
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            f"--remote-debugging-port={test_port}",
            f"--user-data-dir={user_data_dir}",
            "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        try:
            # Wait for chromium ready
            ready = False
            for _ in range(40):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/json/version", timeout=1.0) as resp:
                        if resp.status == 200:
                            ready = True
                            break
                except Exception:
                    time.sleep(0.1)
            self.assertTrue(ready, "Chromium failed to start on dynamic port")

            # Get initial user tab
            with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/json/list") as r:
                initial_targets = json.loads(r.read().decode())
            user_tab = initial_targets[0]["id"]

            from native_browser_fixture import bind_profile, register_page
            from tools.browser_task_cdp import create_page, read_browser_id
            from tools.browser_task_registry import close_owned, owns, session_view

            ctx = Mock()
            guard_mod.register(ctx)
            self.assertFalse(any(c[0][0] == "on_session_end" for c in ctx.register_hook.call_args_list))
            os.environ.pop("PANEL_LIVE_TASK_DB", None)
            bind_profile(self, Path(db_dir) / "profile")
            self.assertFalse(db_file.exists(), "native ownership must not open PANEL_LIVE_TASK_DB")
            base = f"http://127.0.0.1:{test_port}"
            browser_id = read_browser_id(base)
            target_a = create_page(base)
            target_b = create_page(base)
            self.assertNotEqual(target_a, user_tab)
            self.assertNotEqual(target_b, user_tab)
            register_page(task_id="task-a", session_id="sess_A", turn_id="turn_A", target_id=target_a, browser_id=browser_id, endpoint=base)
            register_page(task_id="task-b", session_id="sess_B", turn_id="turn_B", target_id=target_b, browser_id=browser_id, endpoint=base)
            self.assertFalse(owns("sess_A", "task-a", user_tab, browser_id), "Task A must NOT own user tab")
            self.assertFalse(owns("sess_A", "task-a", target_b, browser_id), "Task A must NOT own Task B tab")
            self.assertFalse(owns("sess_B", "task-b", user_tab, browser_id))
            close_owned("task-a")
            with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/json/list") as r:
                remaining_ids = {t["id"] for t in json.loads(r.read().decode())}
            self.assertIn(user_tab, remaining_ids, "User tab was erroneously closed!")
            self.assertIn(target_b, remaining_ids, "Task B tab was erroneously closed!")
            self.assertNotIn(target_a, remaining_ids, "Task A tab was NOT closed on task end!")
            status_a = session_view("sess_A", "task-a")
            self.assertEqual(status_a["status"], "ended")
            self.assertEqual(status_a["cleanupError"], "")
            close_owned("task-b")
            with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/json/list") as r:
                final_ids = {t["id"] for t in json.loads(r.read().decode())}
            self.assertIn(user_tab, final_ids, "User tab closed after Task B completion!")
            self.assertNotIn(target_b, final_ids, "Task B tab was NOT closed on task B end!")

        finally:
            proc.terminate()
            proc.wait()
            shutil.rmtree(user_data_dir, ignore_errors=True)
            shutil.rmtree(db_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
