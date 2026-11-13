"""Tests for the clawd-native panel update (clawd_server/update_native.py).

Covers version/channel selection, the release check (mocked GitHub),
start_update validation, the run-dir bookkeeping (fake systemctl, real
local git fetch), and the update runner (fake systemctl/curl). No test
touches the network or real systemd units.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from clawd_server import update_native


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        capture_output=True, text=True, check=True, timeout=30,
    )


def _release(tag: str, prerelease: bool) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": False,
        "html_url": f"https://github.com/J606y/clawd/releases/tag/{tag}",
        "body": f"Release {tag}",
        "published_at": "2026-11-13T00:00:00Z",
    }


class UpdateNativeTest(unittest.TestCase):
    def setUp(self) -> None:
        update_native._CHECK_CACHE.clear()
        update_native._LAST_CLEARED_RUN = None
        self.tmp = Path(tempfile.mkdtemp(prefix="clawd-update-test-"))
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        (self.home / "panel").mkdir(parents=True)
        (self.repo / "apps" / "panel" / "src" / "react" / "lib").mkdir(parents=True)
        (self.repo / "apps" / "panel" / "dist").mkdir(parents=True)
        (self.repo / "apps" / "panel" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
        (self.repo / "apps" / "panel" / "src" / "react" / "lib" / "version.ts").write_text(
            'export const CLAWD_VERSION = "C0.0.1";\n', encoding="utf-8",
        )
        (self.repo / "clawd_server").mkdir(parents=True)
        shutil.copyfile(
            Path(update_native.__file__).resolve().parent / "update_runner.sh",
            self.repo / "clawd_server" / "update_runner.sh",
        )
        _git(self.repo, "init")
        _git(self.repo, "checkout", "-b", "clawd")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "base")
        self.head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        # A local "origin" one commit ahead, tagged C0.0.2, for real fetches.
        self.origin = self.tmp / "origin"
        _git(self.tmp, "clone", str(self.repo), str(self.origin))
        (self.origin / "next.txt").write_text("next", encoding="utf-8")
        _git(self.origin, "add", "-A")
        _git(self.origin, "commit", "-m", "next")
        _git(self.origin, "tag", "C0.0.2")
        self.tag_commit = _git(self.origin, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "remote", "add", "origin", str(self.origin))
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.sysctl_log = self.tmp / "systemctl.log"
        (self.bin_dir / "systemctl").write_text(
            "#!/bin/sh\n"
            'echo "$@" >> "$FAKE_SYSTEMCTL_LOG"\n'
            'if [ "$1" = "is-active" ]; then exit 1; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        (self.bin_dir / "systemctl").chmod(0o755)
        # Fake systemd-run too: the launch must never touch real systemd.
        (self.bin_dir / "systemd-run").write_text(
            "#!/bin/sh\n"
            'echo "$@" >> "$FAKE_SYSTEMCTL_LOG"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        (self.bin_dir / "systemd-run").chmod(0o755)
        self._env = mock.patch.dict(os.environ, {
            "PATH": str(self.bin_dir) + os.pathsep + os.environ["PATH"],
            "FAKE_SYSTEMCTL_LOG": str(self.sysctl_log),
        })
        self._env.start()
        self.addCleanup(self._env.stop)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mod(self) -> Any:
        return SimpleNamespace(
            ApiError=ApiError,
            HERMES_HOME=str(self.home),
            HERMES_INSTALL=str(self.repo),
            SERVICES=[],
            redact=lambda text: text,
            _backup_local_changes=self._backup,
        )

    def _backup(self, run: Path) -> None:
        (run / "old-head").write_text(self.head + "\n", encoding="ascii")

    def _mock_releases(self, releases: list[dict[str, Any]]) -> Any:
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        resp.read.return_value = json.dumps(releases).encode("utf-8")
        return mock.patch.object(update_native.urllib.request, "urlopen", return_value=resp)

    def _mock_http_error(self, code: int) -> Any:
        err = urllib.error.HTTPError("https://api.github.com/", code, "err", {}, None)
        return mock.patch.object(update_native.urllib.request, "urlopen", side_effect=err)

    def test_parse_version(self) -> None:
        self.assertEqual(update_native.parse_version("0.0.2"), ("stable", (0, 0, 2)))
        self.assertEqual(update_native.parse_version("v1.2.3"), ("stable", (1, 2, 3)))
        self.assertEqual(update_native.parse_version("C0.0.1"), ("beta", (0, 0, 1)))
        self.assertIsNone(update_native.parse_version("main"))
        self.assertIsNone(update_native.parse_version(""))
        self.assertIsNone(update_native.parse_version("C0.0"))

    def test_select_release(self) -> None:
        releases = [_release("0.0.2", False), _release("C0.0.2", True), _release("C0.0.3", True)]
        self.assertEqual(update_native.select_release(releases, "stable")["tag_name"], "0.0.2")
        self.assertEqual(update_native.select_release(releases, "beta")["tag_name"], "C0.0.3")
        self.assertIsNone(update_native.select_release([_release("C0.0.2", True)], "stable"))
        self.assertIsNone(update_native.select_release([_release("0.0.2", True)], "stable"))
        self.assertIsNone(update_native.select_release([], "beta"))

    def test_channel_round_trip(self) -> None:
        self.assertEqual(update_native.read_channel(self.home), "stable")
        update_native.write_channel(self.home, "beta")
        self.assertEqual(update_native.read_channel(self.home), "beta")
        update_native.write_channel(self.home, "stable")
        self.assertEqual(update_native.read_channel(self.home), "stable")
        with self.assertRaises(ValueError):
            update_native.write_channel(self.home, "nightly")
        (self.home / "panel" / "update-channel.json").write_text("{bad json", encoding="utf-8")
        self.assertEqual(update_native.read_channel(self.home), "stable")

    def test_current_version(self) -> None:
        self.assertEqual(update_native.current_version(self.repo), "C0.0.1")

    def test_get_update_info_reports_auth_error_without_token(self) -> None:
        mod = self._mod()
        with self._mock_http_error(404):
            info = update_native.get_update_info(mod)
        self.assertEqual(info["channel"], "stable")
        self.assertIsNone(info["behindBy"])
        self.assertIn("凭证", info["error"])

    def test_get_update_info_finds_newer_release_per_channel(self) -> None:
        mod = self._mod()
        releases = [_release("0.0.2", False), _release("C0.0.2", True)]
        with self._mock_releases(releases):
            beta = update_native.get_update_info(mod, channel="beta")
        self.assertEqual(beta["latestVersion"], "C0.0.2")
        self.assertEqual(beta["behindBy"], 1)
        self.assertEqual(beta["channel"], "beta")
        self.assertEqual(update_native.read_channel(self.home), "beta")
        with self._mock_releases(releases):
            stable = update_native.get_update_info(mod, channel="stable")
        self.assertEqual(stable["latestVersion"], "0.0.2")
        self.assertEqual(stable["behindBy"], 1)

    def test_get_update_plan(self) -> None:
        mod = self._mod()
        releases = [_release("C0.0.2", True)]
        with self._mock_releases(releases):
            plan = update_native.get_update_plan(mod)
        self.assertEqual(plan["exitCode"], 0)
        with self._mock_releases([_release("C0.0.1", True)]):
            plan = update_native.get_update_plan(mod)
        self.assertIn("已是该通道的最新版", plan["plan"])

    def test_get_update_status_idle(self) -> None:
        mod = self._mod()
        status = update_native.get_update_status(mod)
        self.assertFalse(status["running"])
        self.assertEqual(status["state"], "idle")
        self.assertIsNone(status["runId"])

    def test_install_update_binds(self) -> None:
        mod = SimpleNamespace(ApiError=ApiError)
        update_native.install_update(mod)
        self.assertTrue(mod._clawd_update_native)
        self.assertTrue(callable(mod.get_update_info))
        self.assertTrue(callable(mod.start_update))
        self.assertTrue(callable(mod.get_update_plan))
        self.assertTrue(callable(mod.get_update_status))

    def test_start_update_requires_confirmation(self) -> None:
        mod = self._mod()
        with self.assertRaises(ApiError) as ctx:
            update_native.start_update(mod, "nope")
        self.assertEqual(ctx.exception.code, "not_confirmed")
        self.assertEqual(ctx.exception.status, 400)

    def test_start_update_refuses_wrong_branch(self) -> None:
        mod = self._mod()
        _git(self.repo, "checkout", "-b", "other")
        with self.assertRaises(ApiError) as ctx:
            update_native.start_update(mod, "update", "C0.0.2", "beta")
        self.assertEqual(ctx.exception.code, "wrong_branch")

    def test_start_update_refuses_dirty_tree(self) -> None:
        mod = self._mod()
        (self.repo / "apps" / "panel" / "src" / "react" / "lib" / "version.ts").write_text("dirty", encoding="utf-8")
        with self.assertRaises(ApiError) as ctx:
            update_native.start_update(mod, "update", "C0.0.2", "beta")
        self.assertEqual(ctx.exception.code, "local_changes")

    def test_start_update_refuses_unknown_tag(self) -> None:
        mod = self._mod()
        with self._mock_releases([_release("C0.0.2", True)]):
            with self.assertRaises(ApiError) as ctx:
                update_native.start_update(mod, "update", "C0.0.9", "beta")
        self.assertEqual(ctx.exception.code, "unknown_release")
        self.assertEqual(ctx.exception.status, 404)

    def test_start_update_refuses_cross_channel_tag(self) -> None:
        mod = self._mod()
        with self._mock_releases([_release("C0.0.2", True)]):
            with self.assertRaises(ApiError) as ctx:
                update_native.start_update(mod, "update", "C0.0.2", "stable")
        self.assertEqual(ctx.exception.code, "wrong_channel")

    def test_start_update_refuses_already_latest(self) -> None:
        mod = self._mod()
        with self._mock_releases([_release("C0.0.1", True)]):
            with self.assertRaises(ApiError) as ctx:
                update_native.start_update(mod, "update", "C0.0.1", "beta")
        self.assertEqual(ctx.exception.code, "already_latest")

    def test_start_update_launches_and_records_run(self) -> None:
        mod = self._mod()
        with self._mock_releases([_release("C0.0.2", True)]):
            result = update_native.start_update(mod, "update", "C0.0.2", "beta")
        self.assertTrue(result["ok"])
        run = self.home / "panel-updates" / result["runId"]
        self.assertTrue((run / "target.json").exists())
        target = json.loads((run / "target.json").read_text(encoding="utf-8"))
        self.assertEqual(target["tag"], "C0.0.2")
        self.assertEqual(target["channel"], "beta")
        self.assertTrue((run / "runner.sh").exists())
        self.assertTrue(os.access(run / "runner.sh", os.X_OK))
        self.assertTrue((run / "dist-backup" / "index.html").exists())
        self.assertTrue((run / "old-head").exists())
        self.assertFalse((run / ".git-credentials").exists())
        log = self.sysctl_log.read_text(encoding="utf-8") if self.sysctl_log.exists() else ""
        self.assertIn("--unit=clawd-panel-update", log)
        self.assertIn("clawd-panel-update", log)
        # The tag really was fetched into the repo.
        fetched = _git(self.repo, "rev-parse", "C0.0.2^{commit}").stdout.strip()
        self.assertEqual(fetched, self.tag_commit)


class UpdateRunnerTest(unittest.TestCase):
    """The detached runner: reset, restart, health-check, rollback."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clawd-runner-test-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        (self.repo / "apps" / "panel" / "dist").mkdir(parents=True)
        (self.repo / "apps" / "panel" / "dist" / "index.html").write_text("old", encoding="utf-8")
        shutil.copyfile(
            Path(update_native.__file__).resolve().parent / "update_runner.sh",
            self.tmp / "runner.sh",
        )
        (self.tmp / "runner.sh").chmod(0o755)
        _git(self.repo, "init")
        _git(self.repo, "checkout", "-b", "clawd")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "old")
        self.old_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        (self.repo / "new.txt").write_text("new", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "new")
        self.new_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "reset", "--hard", self.old_head)
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        (self.bin_dir / "systemctl").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.bin_dir / "systemctl").chmod(0o755)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_curl(self, code: int) -> None:
        (self.bin_dir / "curl").write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
        (self.bin_dir / "curl").chmod(0o755)

    def _run_runner(self) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PATH"] = str(self.bin_dir) + os.pathsep + env["PATH"]
        env["CLAWD_PANEL_UNIT"] = "fake-panel"
        env["CLAWD_UPDATE_HEALTH_TRIES"] = "2"
        return subprocess.run(
            ["/bin/sh", str(self.tmp / "runner.sh"), str(self.run_dir), self.new_head, self.old_head],
            cwd=self.repo, env=env, capture_output=True, text=True, timeout=60, check=False,
        )

    def _head(self) -> str:
        return _git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def test_runner_success_moves_head(self) -> None:
        self._fake_curl(0)
        proc = self._run_runner()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._head(), self.new_head)
        self.assertIn("update complete", proc.stdout)

    def test_runner_rolls_back_on_failed_health(self) -> None:
        self._fake_curl(1)
        proc = self._run_runner()
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self._head(), self.old_head)
        self.assertIn("rolling back", proc.stdout)


if __name__ == "__main__":
    unittest.main()
