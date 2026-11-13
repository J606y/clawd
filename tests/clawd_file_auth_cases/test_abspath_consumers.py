"""absPath returned by upload/list must stay usable by the real recheck."""

from __future__ import annotations

from pathlib import Path

from tests.clawd_file_auth_cases.helpers import (
    assert_under,
    isolated_panel_core,
    read_artifact,
    upload,
)


def _roots(home: Path) -> list[str]:
    from clawd_server.server import _artifact_roots

    return _artifact_roots(home)


def test_upload_abspath_matches_consumer_shape_and_recheck(tmp_path: Path):
    payload = b"%PDF-hello"
    with isolated_panel_core(tmp_path) as (home, mod):
        result = upload(mod, payload, "报告.txt")
        assert result["root"] == "uploads"
        assert isinstance(result["path"], str) and not result["path"].startswith("/")
        assert ".." not in Path(result["path"]).parts
        assert result["size"] == len(payload)
        abs_path = result["absPath"]
        assert abs_path.startswith("/")
        assert not any(part in {".", ".."} for part in Path(abs_path).parts)
        assert_under(Path(abs_path), home)
        assert any(abs_path.startswith(root) for root in _roots(home)), _roots(home)
        assert Path(abs_path).read_bytes() == payload
        assert read_artifact(mod, abs_path) == payload
        listed = mod.list_dir("uploads", str(Path(result["path"]).parent))
        joined = listed["absPath"].rstrip("/") + "/" + Path(abs_path).name
        assert Path(joined).read_bytes() == payload
        assert read_artifact(mod, joined) == payload


def test_cron_and_images_abspath_use_injected_roots(tmp_path: Path):
    with isolated_panel_core(tmp_path) as (home, mod):
        roots = _roots(home)
        for relative in ("cron/output/job.txt", "images/pic.txt"):
            path = home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode())
            text = str(path)
            assert any(text.startswith(root) for root in roots), (text, roots)
            assert read_artifact(mod, text) == relative.encode()
        assert not any(root.startswith("/root/hermes-") or "/root/.hermes" in root for root in roots)


def test_symlinked_home_abspath_stays_consumable(tmp_path: Path):
    real = tmp_path / "real-home"
    real.mkdir()
    link = tmp_path / "link-home"
    link.symlink_to(real, target_is_directory=True)
    with isolated_panel_core(tmp_path, home=link) as (home, mod):
        result = upload(mod, b"linked", "note.txt")
        abs_path = result["absPath"]
        roots = _roots(home)
        assert any(abs_path.startswith(root) for root in roots), (abs_path, roots)
        assert read_artifact(mod, abs_path) == b"linked"
        assert_under(Path(abs_path), real)
