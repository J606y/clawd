"""Encoded traversal through Handler._dispatch, which unquotes once."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.clawd_file_auth_cases.helpers import (
    assert_payload_absent,
    dispatch,
    error_code,
    isolated_panel_core,
)

OUTSIDE = b"ENCODED-MUST-NOT-LEAK"


def _outside(tmp_path: Path) -> Path:
    path = tmp_path / "outside-encoded.txt"
    path.write_bytes(OUTSIDE)
    return path


@pytest.mark.parametrize(
    "query",
    [
        "path=%2e%2e%2fsecret",
        "path=%2e%2e/%2e%2e/secret",
        "path=%2e%2e%5csecret",
        "path=%00secret",
        "path=folder%2f%2e%2e%2fsecret",
    ],
)
def test_download_single_decode_cannot_escape(tmp_path: Path, query: str):
    _outside(tmp_path)
    with isolated_panel_core(tmp_path) as (home, mod):
        (home / "uploads" / "public.txt").write_bytes(b"public")
        status, _headers, body = dispatch(mod, "GET", f"/files/download?root=uploads&{query}")
        assert status in {400, 403}
        assert status != 401
        assert OUTSIDE not in body
        assert error_code(body) != "unauthorized"
        assert_payload_absent(home, OUTSIDE)


def test_double_encoding_is_not_decoded_into_parent(tmp_path: Path):
    _outside(tmp_path)
    with isolated_panel_core(tmp_path) as (home, mod):
        literal = home / "uploads" / "%2e%2e%2fsecret"
        literal.write_bytes(b"literal-name")
        status, _headers, body = dispatch(
            mod, "GET", "/files/download?root=uploads&path=%252e%252e%252fsecret"
        )
        assert OUTSIDE not in body
        assert status != 401
        if status == 200:
            assert body == b"literal-name"


def test_fullwidth_and_overlong_dots_do_not_escape(tmp_path: Path):
    _outside(tmp_path)
    with isolated_panel_core(tmp_path) as (_home, mod):
        for query in ("path=%ef%bc%8e%ef%bc%8e%2fsecret", "path=%c0%ae%c0%ae%2fsecret"):
            status, _headers, body = dispatch(mod, "GET", f"/files/download?root=uploads&{query}")
            assert status != 200
            assert status != 401
            assert OUTSIDE not in body


def test_upload_dir_encoding_cannot_write_outside(tmp_path: Path):
    payload = b"UPLOAD-ENCODED-MUST-NOT-ESCAPE"
    outside = tmp_path / "outside-upload"
    outside.mkdir()
    with isolated_panel_core(tmp_path) as (_home, mod):
        status, _headers, body = dispatch(
            mod,
            "POST",
            "/files/upload?root=uploads&name=note.txt&dir=%2e%2e%2f%2e%2e%2foutside-upload",
            body=payload,
        )
        assert status in {400, 403}
        assert status != 401
        assert error_code(body) != "unauthorized"
        assert_payload_absent(outside, payload)


def test_list_encoding_cannot_escape(tmp_path: Path):
    _outside(tmp_path)
    with isolated_panel_core(tmp_path) as (_home, mod):
        status, _headers, body = dispatch(mod, "GET", "/files?root=uploads&path=%2e%2e%2f%2e%2e")
        assert status in {400, 403}
        assert status != 401
        assert OUTSIDE not in body


def test_artifact_encoded_parent_is_rejected(tmp_path: Path):
    with isolated_panel_core(tmp_path) as (home, mod):
        status, _headers, body = dispatch(
            mod,
            "GET",
            "/files/artifact?path=" + str(home / "uploads").replace("/", "%2F") + "%2F%2e%2e%2Fsecret",
        )
        assert status in {400, 403}
        assert status != 401
        assert error_code(body) != "unauthorized"


def test_encoded_dotfile_name_is_rejected(tmp_path: Path):
    payload = b"ENCODED-DOTFILE"
    with isolated_panel_core(tmp_path) as (home, mod):
        status, _headers, body = dispatch(
            mod, "POST", "/files/upload?root=uploads&name=%2eenv", body=payload
        )
        assert status in {400, 403}, body
        assert status != 401
        assert_payload_absent(home, payload)
