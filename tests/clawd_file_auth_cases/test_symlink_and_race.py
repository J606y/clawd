"""Symlink escape and replacement races through the bound sidecar path entry."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from tests.clawd_file_auth_cases.helpers import (
    assert_payload_absent,
    assert_under,
    isolated_sidecar,
    make_handler,
    upload,
)

OUTSIDE = b"MUST-NOT-LEAK"


def test_bind_refuses_symlinked_upload_root(tmp_path: Path):
    outside = tmp_path / "outside-root"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(OUTSIDE)
    with isolated_sidecar(tmp_path, bind_now=False) as (home, mod):
        (home / "uploads").symlink_to(outside, target_is_directory=True)
        from clawd_server.bind import bind, clawd_command

        with pytest.raises(RuntimeError, match="outside CLAWD_HOME"):
            bind(mod, home, clawd_command(home))
        assert (outside / "secret.txt").read_bytes() == OUTSIDE
        assert_payload_absent(outside, b"clawd")


def test_preexisting_symlink_cannot_escape_or_open(tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(OUTSIDE)
    with isolated_sidecar(tmp_path) as (home, mod):
        (home / "uploads" / "escape.txt").symlink_to(outside)
        with pytest.raises(mod.ApiError) as escaped:
            mod._safe_path("uploads", "escape.txt")
        assert escaped.value.status == 403
        assert escaped.value.status != 401
        with pytest.raises(mod.ApiError) as opened:
            with mod._open_download("uploads", "escape.txt"):
                pass
        assert opened.value.status != 401
        listed = mod.list_dir("uploads", "")
        assert "escape.txt" not in {item["name"] for item in listed["entries"]}
        assert_under(Path(listed["absPath"]), home)


def test_in_root_symlink_is_not_opened(tmp_path: Path):
    with isolated_sidecar(tmp_path) as (home, mod):
        target = home / "uploads" / "report.txt"
        target.write_bytes(b"safe")
        (home / "uploads" / "alias.txt").symlink_to(target)
        with pytest.raises(mod.ApiError) as exc:
            with mod._open_download("uploads", "alias.txt"):
                pass
        assert exc.value.status == 403
        assert exc.value.status != 401


def test_symlink_loop_fails_closed(tmp_path: Path):
    with isolated_sidecar(tmp_path) as (home, mod):
        loop = home / "uploads" / "loop.txt"
        loop.symlink_to(loop)
        with pytest.raises(mod.ApiError) as exc:
            with mod._open_download("uploads", "loop.txt"):
                pass
        assert exc.value.status == 403


def test_file_swap_after_validation_does_not_return_outside_bytes(tmp_path: Path):
    outside = tmp_path / "outside-secret.txt"
    outside.write_bytes(OUTSIDE)
    with isolated_sidecar(tmp_path) as (home, mod):
        path = home / "uploads" / "report.txt"
        path.write_bytes(b"safe")
        real = mod._safe_path

        def swap(root_id, rel):
            result = real(root_id, rel)
            path.unlink()
            path.symlink_to(outside)
            return result

        with mock.patch.object(mod, "_safe_path", side_effect=swap):
            with pytest.raises(mod.ApiError) as exc:
                with mod._open_download("uploads", "report.txt") as (stream, _info, _lexical):
                    pytest.fail(f"swapped file was read: {stream.read()!r}")
        assert exc.value.status == 403
        assert exc.value.status != 401
        assert OUTSIDE not in exc.value.message.encode()


def test_directory_swap_after_validation_does_not_return_outside_bytes(tmp_path: Path):
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "report.txt").write_bytes(OUTSIDE)
    with isolated_sidecar(tmp_path) as (home, mod):
        folder = home / "uploads" / "nested"
        folder.mkdir()
        (folder / "report.txt").write_bytes(b"safe")
        real = mod._safe_path

        def swap(root_id, rel):
            result = real(root_id, rel)
            folder.rename(home / "uploads" / "nested-real")
            folder.symlink_to(outside, target_is_directory=True)
            return result

        with mock.patch.object(mod, "_safe_path", side_effect=swap):
            with pytest.raises(mod.ApiError) as exc:
                with mod._open_download("uploads", "nested/report.txt") as (stream, _info, _lexical):
                    pytest.fail(f"swapped directory was read: {stream.read()!r}")
        assert exc.value.status == 403
        assert OUTSIDE not in exc.value.message.encode()


def test_upload_directory_swap_does_not_write_outside(tmp_path: Path):
    payload = b"UPLOAD-MUST-NOT-ESCAPE"
    outside = tmp_path / "outside-upload"
    outside.mkdir()
    with isolated_sidecar(tmp_path) as (home, mod):
        folder = home / "uploads" / "inbox"
        folder.mkdir()
        real = mod._safe_path

        def swap(root_id, rel):
            result = real(root_id, rel)
            if folder.exists() and not folder.is_symlink():
                folder.rename(home / "uploads" / "inbox-real")
                folder.symlink_to(outside, target_is_directory=True)
            return result

        handler = make_handler(mod, headers={"Content-Length": str(len(payload))}, body=payload)
        with mock.patch.object(mod, "_safe_path", side_effect=swap):
            try:
                result = handler._upload({"root": "uploads", "name": "note.txt", "dir": "inbox"})
            except mod.ApiError as exc:
                assert exc.status != 401
            else:
                assert_under(Path(result["absPath"]), home)
        assert_payload_absent(outside, payload)
        assert not list(outside.glob(".upload-*"))


def test_root_swap_after_bind_does_not_list_or_write_outside(tmp_path: Path):
    payload = b"ROOT-SWAP-MUST-NOT-ESCAPE"
    outside = tmp_path / "outside-after-bind"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(OUTSIDE)
    with isolated_sidecar(tmp_path) as (home, mod):
        uploads = home / "uploads"
        uploads.rename(home / "uploads-real")
        uploads.symlink_to(outside, target_is_directory=True)
        errors: list[str] = []
        try:
            listed = mod.list_dir("uploads", "")
        except mod.ApiError as exc:
            if exc.status == 401:
                errors.append("list relabeled 401")
            elif exc.status != 403:
                errors.append(f"list status {exc.status}")
        else:
            errors.append(f"listed {listed.get('absPath')}")
        try:
            result = upload(mod, payload, "note.txt")
        except mod.ApiError as exc:
            if exc.status == 401:
                errors.append("upload relabeled 401")
        else:
            errors.append(f"uploaded {result.get('absPath')}")
        try:
            with mod._open_download("uploads", "secret.txt") as (stream, _info, _lexical):
                errors.append(f"opened {stream.read()!r}")
        except mod.ApiError as exc:
            if exc.status == 401:
                errors.append("open relabeled 401")
        leaked = payload in b"".join(
            path.read_bytes() for path in outside.iterdir() if path.is_file() and not path.is_symlink()
        )
        if leaked:
            errors.append("payload written outside")
        if (outside / "secret.txt").read_bytes() != OUTSIDE:
            errors.append("outside file changed")
        if errors:
            pytest.fail("root swap: " + "; ".join(errors))
