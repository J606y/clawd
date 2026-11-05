"""Upload size and interrupted body handling on Handler._upload."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from tests.clawd_file_auth_cases.helpers import assert_payload_absent, isolated_sidecar, make_handler


class CountingReader(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.reads = 0

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        return super().read(size)


def test_over_limit_is_413_before_read_and_leaves_no_file(tmp_path: Path):
    with isolated_sidecar(tmp_path) as (home, mod):
        reader = CountingReader(b"x")
        handler = make_handler(mod, headers={"Content-Length": str(mod.MAX_UPLOAD_BYTES + 1)})
        handler.rfile = reader
        with pytest.raises(mod.ApiError) as exc:
            handler._upload({"root": "uploads", "name": "big.bin"})
        assert exc.value.status == 413
        assert exc.value.code == "too_large"
        assert exc.value.status != 401
        assert reader.reads == 0
        assert_payload_absent(home, b"x")
        assert not list((home / "uploads").rglob(".upload-*"))


def test_missing_length_is_411_not_401(tmp_path: Path):
    with isolated_sidecar(tmp_path) as (_home, mod):
        handler = make_handler(mod, headers={})
        with pytest.raises(mod.ApiError) as exc:
            handler._upload({"root": "uploads", "name": "a.txt"})
        assert exc.value.status == 411
        assert exc.value.code == "length_required"
        assert exc.value.status != 401


def test_interrupted_body_removes_partial_file(tmp_path: Path):
    payload = b"PARTIAL-MUST-NOT-REMAIN"
    with isolated_sidecar(tmp_path) as (home, mod):
        handler = make_handler(
            mod,
            headers={"Content-Length": str(len(payload) + 20)},
            body=payload,
        )
        with pytest.raises(mod.ApiError) as exc:
            handler._upload({"root": "uploads", "name": "cut.txt", "dir": "inbox"})
        assert exc.value.status == 400
        assert exc.value.code == "incomplete"
        assert exc.value.status != 401
        assert_payload_absent(home, payload)
        assert not list((home / "uploads").rglob(".upload-*"))
        assert not (home / "uploads" / "inbox" / "cut.txt").exists()


def test_read_only_root_is_403_not_401(tmp_path: Path):
    with isolated_sidecar(tmp_path) as (_home, mod):
        handler = make_handler(mod, headers={"Content-Length": "1"}, body=b"x")
        with pytest.raises(mod.ApiError) as exc:
            handler._upload({"root": "cron", "name": "out.txt"})
        assert exc.value.status == 403
        assert exc.value.code == "read_only"
        assert exc.value.status != 401


def test_small_upload_still_commits(tmp_path: Path):
    with isolated_sidecar(tmp_path) as (home, mod):
        handler = make_handler(mod, headers={"Content-Length": "4"}, body=b"okay")
        result = handler._upload({"root": "uploads", "name": "note.txt"})
        assert result["size"] == 4
        assert Path(result["absPath"]).read_bytes() == b"okay"
        assert not list((home / "uploads").rglob(".upload-*"))
