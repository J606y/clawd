"""Packaging and direct-entry checks for the clawd checkout.

Builds a real wheel and refuses deleted desktop entrypoints. Does not install
the wheel or touch a production Hermes home.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
NODE = REPO / ".dev-home/tools/node-26.12.1-linux-x64/bin/node"
REFUSAL = "refused:"


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMPDIR": str(tmp_path / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "CLAWD_HOME": str(tmp_path / "clawd-data"),
        "HERMES_HOME": str(tmp_path / "clawd-data"),
    }


def test_setuptools_include_ships_clawd_service_packages():
    include = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["setuptools"]["packages"]["find"]["include"]
    for name in ("clawd_cli", "clawd_server", "clawd_projects"):
        assert name in include
    assert "apps/panel" not in include
    assert "apps/desktop" not in include


def test_wheel_contains_clawd_server_and_projects(tmp_path: Path):
    out = tmp_path / "wheel"
    build_tmp = tmp_path / "build-tmp"
    out.mkdir()
    build_tmp.mkdir()
    env = _isolated_env(tmp_path)
    env["TMPDIR"] = str(build_tmp)
    env["HERMES_NIX_BUILD"] = "1"
    env["PYTHONPATH"] = str(REPO)
    proc = subprocess.run(
        [
            str(PYTHON),
            "-c",
            "import os, sys; os.environ['HERMES_NIX_BUILD']='1'; "
            "from setuptools.build_meta import build_wheel; print(build_wheel(sys.argv[1]))",
            str(out),
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, wheels
    names = zipfile.ZipFile(wheels[0]).namelist()
    for member in (
        "clawd_server/__init__.py",
        "clawd_server/__main__.py",
        "clawd_projects/__init__.py",
        "clawd_projects/service.py",
        "clawd_cli/__init__.py",
        "clawd_cli/main.py",
    ):
        assert member in names, member
    assert not any(name.startswith("apps/desktop/") for name in names)
    assert not any(name.startswith("apps/panel/") for name in names)


DIRECT_ENTRIES = (
    "scripts/bundles/desktop.py",
    "scripts/bundles/desktop_prepare.py",
    "scripts/bundles/desktop_toolchain.py",
    "scripts/ci/desktop_commands.py",
    "scripts/ci/desktop_build_cache.py",
    "scripts/dump_desktop_slash_registry.py",
    "scripts/desktop-update/serve-ui.py",
)


@pytest.mark.parametrize("relative", DIRECT_ENTRIES)
def test_deleted_desktop_python_entries_refuse(tmp_path: Path, relative: str):
    env = _isolated_env(tmp_path)
    before = set((REPO / ".build").glob("*")) if (REPO / ".build").exists() else set()
    proc = subprocess.run(
        [str(PYTHON), str(REPO / relative), "--help"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 2, proc.stderr
    assert REFUSAL in proc.stderr.lower()
    assert "hermes update" not in proc.stdout.lower()
    after = set((REPO / ".build").glob("*")) if (REPO / ".build").exists() else set()
    assert after == before


def test_deleted_desktop_shell_entries_refuse(tmp_path: Path):
    env = _isolated_env(tmp_path)
    for relative in (
        "scripts/desktop-update/posix.sh",
        "scripts/desktop-update/repro.sh",
        "scripts/build-bundle.sh",
    ):
        proc = subprocess.run(
            ["bash", str(REPO / relative), "--help"],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert proc.returncode == 2, (relative, proc.stderr)
        assert REFUSAL in proc.stderr.lower()


def test_tui_launch_env_drops_hermes_bin(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAWD_CLI", "1")
    monkeypatch.setenv("HERMES_BIN", "/usr/local/bin/hermes")
    monkeypatch.setenv("CLAWD_BIN", str(REPO / "clawd"))
    from hermes_cli.main_tui_launch import _pin_clawd_tui_command, _product_command

    env = {"HERMES_BIN": "/usr/local/bin/hermes", "PATH": "/usr/local/bin"}
    _pin_clawd_tui_command(env)
    assert _product_command() == "clawd"
    assert "HERMES_BIN" not in env
    assert env["CLAWD_BIN"] == str((REPO / "clawd").resolve())
    assert env["CLAWD_CLI"] == "1"


def test_tui_update_exit_does_not_relaunch_hermes(monkeypatch, capsys):
    monkeypatch.setenv("CLAWD_CLI", "1")
    from hermes_cli.main_tui_launch import _refuse_upstream_update

    with pytest.raises(SystemExit) as exc:
        _refuse_upstream_update()
    assert exc.value.code == 2
    assert "refused:" in capsys.readouterr().err.lower()


def test_external_command_uses_clawd_not_hermes_binary(tmp_path: Path):
    if not NODE.is_file():
        pytest.skip(f"node toolchain missing: {NODE}")
    script = """
import { productCommandArgv } from 'PROBE_IMPORT'
process.env.CLAWD_CLI = '1'
process.env.HERMES_PYTHON = process.env.PROBE_PYTHON
process.env.HERMES_PYTHON_SRC_ROOT = process.env.PROBE_ROOT
process.env.HERMES_BIN = '/usr/local/bin/hermes'
delete process.env.CLAWD_BIN
console.log(JSON.stringify(productCommandArgv(['setup'])))
""".replace("PROBE_IMPORT", (REPO / "ui-tui/src/lib/externalCli.ts").as_posix())
    env = _isolated_env(tmp_path)
    env["PROBE_PYTHON"] = str(PYTHON)
    env["PROBE_ROOT"] = str(REPO)
    proc = subprocess.run(
        [str(NODE), "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    argv = json.loads(proc.stdout.strip().splitlines()[-1])
    assert argv[0] == str(PYTHON)
    assert argv[1] == str(REPO / "clawd")
    assert argv[2] == "setup"
    assert "hermes" not in Path(argv[0]).name
    assert not any(Path(part).name in {"hermes", "hermes.exe"} for part in argv)
