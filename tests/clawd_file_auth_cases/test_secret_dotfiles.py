"""Secret names and dotfiles on the bound file entry, not a patched root."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.clawd_file_auth_cases.helpers import (
    assert_payload_absent,
    isolated_panel_core,
    make_handler,
    read_artifact,
    upload,
)

PAYLOAD = b"SECRET-NAME-BYTES"


@pytest.mark.parametrize(
    "name",
    ["id_rsa", "auth.json", "config.yaml", ".env", ".npmrc", "api_key", "token", "password"],
)
def test_upload_rejects_secret_or_dot_name(tmp_path: Path, name: str):
    with isolated_panel_core(tmp_path) as (home, mod):
        if name in {"api_key", "token", "password"}:
            assert mod._is_secret_name(name)
        try:
            result = upload(mod, PAYLOAD, name)
        except mod.ApiError as exc:
            assert exc.status in {400, 403}
            assert exc.status != 401
            assert exc.code != "unauthorized"
        else:
            pytest.fail(f"{name} stored at {result.get('path')} absPath={result.get('absPath')}")
        assert_payload_absent(home, PAYLOAD)


def test_threshold_tokens_is_not_a_secret_name(tmp_path: Path):
    with isolated_panel_core(tmp_path) as (home, mod):
        assert mod._is_secret_name("threshold_tokens") is False
        result = upload(mod, b"count", "threshold_tokens")
        assert Path(result["absPath"]).read_bytes() == b"count"
        with mod._open_download("uploads", result["path"]) as (stream, _info, _path):
            assert stream.read() == b"count"


@pytest.mark.parametrize(
    "rel",
    [".npmrc", ".git/config", "id_rsa", "api_key", "token", "credentials/a.txt", "tokens/a.txt"],
)
def test_bound_download_and_listing_hide_secret_and_dot_names(tmp_path: Path, rel: str):
    with isolated_panel_core(tmp_path) as (home, mod):
        path = home / "uploads" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PAYLOAD)
        listed = mod.list_dir("uploads", "")
        visible = {item["name"] for item in listed["entries"]}
        errors = []
        if Path(rel).parts[0] in visible:
            errors.append("listed")
        try:
            with mod._open_download("uploads", rel) as (stream, _info, _path):
                if PAYLOAD in stream.read():
                    errors.append("downloaded")
        except mod.ApiError as exc:
            if exc.status == 401 or exc.code == "unauthorized":
                errors.append(f"relabeled {exc.status}")
            elif exc.status not in {400, 403}:
                errors.append(f"status {exc.status}")
        if errors:
            pytest.fail(f"{rel}: {', '.join(errors)}")


@pytest.mark.parametrize(
    "rel",
    [".npmrc", ".git/config", "id_rsa", "api_key", "token", "credentials/a.txt", "client.p12"],
)
def test_artifact_recheck_denies_secret_and_dot_names(tmp_path: Path, rel: str):
    with isolated_panel_core(tmp_path) as (home, mod):
        path = home / "uploads" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PAYLOAD)
        handler = make_handler(mod)
        try:
            handler._artifact(str(path), False)
        except mod.ApiError as exc:
            assert exc.status == 403
            assert exc.status != 401
            assert exc.code != "unauthorized"
            return
        pytest.fail(f"artifact served {rel}")


def test_home_env_symlink_is_denied_without_leaving_temp_home(tmp_path: Path):
    with isolated_panel_core(tmp_path) as (home, mod):
        secret = home / ".env"
        secret.write_bytes(PAYLOAD)
        (home / "uploads" / "alias").symlink_to(secret)
        with pytest.raises(mod.ApiError) as exc:
            mod._safe_path("uploads", "alias")
        assert exc.value.status == 403
        assert exc.value.code == "denied"


def test_bind_installs_production_deny_prefixes_without_opening_them(tmp_path: Path):
    with isolated_panel_core(tmp_path) as (home, mod):
        texts = {str(path) for path in mod.DENY_PREFIXES}
        assert "/etc" in texts
        assert "/root/.ssh" in texts
        assert "/root/.hermes" in texts
        assert "/usr/local/lib/hermes-agent" in texts
        assert str(home / ".env") in texts
        handler = make_handler(mod)
        for absolute in ("/etc/passwd", "/root/.ssh/id_rsa", "/root/.hermes/.env",
                         "/usr/local/lib/hermes-agent/hermes_cli/main.py"):
            with pytest.raises(mod.ApiError) as exc:
                handler._artifact(absolute, False)
            assert exc.value.status == 403
            assert exc.value.status != 401


def test_ordinary_note_still_uploads(tmp_path: Path):
    with isolated_panel_core(tmp_path) as (_home, mod):
        result = upload(mod, b"fine", "credit.txt")
        assert Path(result["absPath"]).read_bytes() == b"fine"
        assert read_artifact(mod, result["absPath"]) == b"fine"
