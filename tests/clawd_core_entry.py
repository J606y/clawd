"""clawd command: help, refused official UI, panel handoff, update refusal."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "clawd"


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(REPO),
        "PYTHONUTF8": "1",
        "CLAWD_HOME": str(tmp_path / "clawd-data"),
        "HERMES_HOME": str(tmp_path / "existing-hermes"),
        "HERMES_RUNTIME_DIR": str(tmp_path / "existing-hermes" / "tools"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra)
    return env


def _run(tmp_path: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args],
        cwd=str(REPO),
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_launcher_exists():
    assert LAUNCHER.is_file()


def test_help_is_clawd_and_keeps_core_commands(tmp_path):
    proc = _run(tmp_path, "--help")
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    assert text.startswith("usage: clawd")
    assert "--tui" in text
    assert "config" in text
    assert "gateway" in text
    assert "panel" in text
    assert "Start the web UI dashboard" not in text
    assert "native desktop app" not in text
    assert not (tmp_path / "existing-hermes").exists()


def test_config_path_uses_clawd_home_not_inherited_hermes(tmp_path):
    proc = _run(tmp_path, "config", "path")
    assert proc.returncode == 0, proc.stderr
    printed = proc.stdout.strip()
    assert printed.startswith(str(tmp_path / "clawd-data"))
    assert "existing-hermes" not in printed
    assert not (tmp_path / "existing-hermes" / "config.yaml").exists()


@pytest.mark.parametrize("command", ["dashboard", "desktop", "gui", "webapp"])
def test_official_ui_commands_are_refused(tmp_path, command):
    proc = _run(tmp_path, command, "--help")
    assert proc.returncode == 2, proc.stderr
    assert "refused:" in proc.stderr.lower()
    assert not (tmp_path / "existing-hermes").exists()


def test_update_is_refused_without_writing_hermes_home(tmp_path, monkeypatch, capsys):
    # In-process: the suite guard blocks a subprocess whose argv contains `update`.
    foreign = tmp_path / "existing-hermes"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(tmp_path / "clawd-data"))
    monkeypatch.setenv("HERMES_HOME", str(foreign))
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(foreign / "tools"))
    monkeypatch.delenv("CLAWD_CLI", raising=False)
    from clawd_cli.main import main

    with pytest.raises(SystemExit) as exc:
        main(["update", "--check"])
    assert exc.value.code == 2
    assert "refused:" in capsys.readouterr().err.lower()
    assert not foreign.exists()
    assert not (tmp_path / "clawd-data" / "logs" / "update_receipts").exists()


def test_panel_forwards_argv_to_clawd_server(monkeypatch):
    from clawd_cli.main import dispatch_panel
    from clawd_server import __main__ as panel_entry

    received = []
    monkeypatch.setattr(sys, "argv", ["clawd"])
    monkeypatch.setattr(panel_entry, "main", lambda argv: received.append(list(argv)))
    dispatch_panel(["serve", "--host", "127.0.0.1"])
    assert received == [["serve", "--host", "127.0.0.1"]]


def test_panel_missing_server_is_a_refusal(monkeypatch, capsys):
    from clawd_cli.main import dispatch_panel

    # Model an actually missing package, not the pre-integration source tree.
    # Calling `panel` without arguments now starts the real long-lived server.
    monkeypatch.setitem(sys.modules, "clawd_server.__main__", None)
    with pytest.raises(SystemExit) as exc:
        dispatch_panel([])
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "refused:" in error.lower()
    assert "clawd_server" in error


def test_integrated_panel_help(tmp_path):
    proc = _run(tmp_path, "panel", "--help")
    assert proc.returncode == 0, proc.stderr
    assert "serve" in proc.stdout
    assert "user" in proc.stdout


def test_official_product_trees_are_gone_and_shared_backend_remains():
    assert not (REPO / "apps" / "desktop").exists()
    assert not (REPO / "web" / "package.json").exists()
    assert (REPO / "apps" / "shared" / "package.json").is_file()
    assert (REPO / "hermes_cli" / "web_server.py").is_file()
    workspaces = json.loads((REPO / "package.json").read_text(encoding="utf-8"))["workspaces"]
    assert "apps/desktop" not in workspaces
    assert "apps/*" not in workspaces
    assert "web" not in workspaces
    assert "apps/shared" in workspaces
    # The panel retains its own lockfile and is built through the root script.
    package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    assert "apps/panel" not in workspaces
    assert package["scripts"]["build:panel"] == "npm --prefix apps/panel --workspaces=false run build"
    assert "ui-tui" in workspaces
    assert "ui-tui/packages/*" in workspaces
    assert "tests-js" in workspaces
