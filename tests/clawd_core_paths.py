"""clawd data-dir isolation. CLAWD_HOME never falls back to an existing Hermes home."""

from pathlib import Path

from hermes_constants import isolate_inherited_hermes_env, resolve_clawd_home


def test_default_home_ignores_inherited_hermes_home(tmp_path):
    env = {
        "HOME": str(tmp_path),
        "HERMES_HOME": str(tmp_path / ".hermes"),
        "HERMES_RUNTIME_DIR": str(tmp_path / ".hermes" / "tools"),
    }
    resolved = resolve_clawd_home(env, user_home=lambda: tmp_path)
    assert resolved == tmp_path / ".clawd"
    summary = isolate_inherited_hermes_env(env, user_home=lambda: tmp_path)
    assert summary["clawd_home"] == str(tmp_path / ".clawd")
    assert env["CLAWD_HOME"] == str(tmp_path / ".clawd")
    assert env["HERMES_HOME"] == str(tmp_path / ".clawd")
    assert env["HERMES_RUNTIME_DIR"] == str(tmp_path / ".clawd" / "tools")
    assert not (tmp_path / ".hermes").exists()


def test_explicit_clawd_home_wins_over_hermes_home(tmp_path):
    clawd = tmp_path / "custom-clawd"
    env = {
        "CLAWD_HOME": str(clawd),
        "HERMES_HOME": str(tmp_path / ".hermes" / "profiles" / "work"),
        "HERMES_RUNTIME_DIR": str(tmp_path / ".hermes" / "tools"),
    }
    summary = isolate_inherited_hermes_env(env, user_home=lambda: tmp_path / "unused")
    assert summary["preserved_profile"] is False
    assert env["HERMES_HOME"] == str(clawd)
    assert env["HERMES_RUNTIME_DIR"] == str(clawd / "tools")


def test_subprocess_clawd_profile_is_preserved(tmp_path):
    root = tmp_path / ".clawd"
    profile = root / "profiles" / ".work.staging-9"
    runtime = root / "profiles" / ".work.staging-9" / "tools"
    env = {
        "HERMES_HOME": str(profile),
        "HERMES_RUNTIME_DIR": str(runtime),
    }
    summary = isolate_inherited_hermes_env(env, user_home=lambda: tmp_path)
    assert summary["preserved_profile"] is True
    assert summary["preserved_runtime"] is True
    assert env["HERMES_HOME"] == str(profile)
    assert env["HERMES_RUNTIME_DIR"] == str(runtime)
    assert env["CLAWD_HOME"] == str(root)


def test_profile_home_does_not_keep_foreign_runtime(tmp_path):
    root = tmp_path / ".clawd"
    env = {
        "CLAWD_HOME": str(root),
        "HERMES_HOME": str(root / "profiles" / "work"),
        "HERMES_RUNTIME_DIR": str(tmp_path / ".hermes" / "tools"),
    }
    summary = isolate_inherited_hermes_env(env, user_home=lambda: tmp_path)
    assert summary["preserved_profile"] is True
    assert summary["preserved_runtime"] is False
    assert env["HERMES_HOME"] == str(root / "profiles" / "work")
    assert env["HERMES_RUNTIME_DIR"] == str(root / "tools")


def test_unset_envs_map_to_clawd_not_platform_hermes(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAWD_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_RUNTIME_DIR", raising=False)
    env = {}
    summary = isolate_inherited_hermes_env(env, user_home=lambda: tmp_path)
    assert Path(summary["hermes_home"]) == tmp_path / ".clawd"
    assert Path(summary["hermes_runtime_dir"]) == tmp_path / ".clawd" / "tools"
    assert ".hermes" not in Path(summary["hermes_home"]).name
