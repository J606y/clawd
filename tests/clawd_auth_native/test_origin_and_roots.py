"""Native origin and artifact-root contracts.

Added for static review of clawd_server.auth and clawd_server.server.
This file was not executed in the change that introduced it. Do not skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clawd_server.auth import origin_allowed
from clawd_server.server import _artifact_roots


def allow(method: str, headers: dict[str, str], peer: str | None) -> bool:
    return origin_allowed(method, headers, peer)


@pytest.mark.parametrize("host", ["[::1]", "[::1]:9"])
def test_bracketed_ipv6_host_matches_origin(host: str):
    headers = {"Host": host, "Origin": "http://[::1]", "Referer": "http://[::1]/panel"}
    assert allow("POST", headers, "10.1.1.1") is True


def test_ipv6_compression_and_case_are_the_same_host():
    assert allow(
        "POST",
        {"Host": "[0:0:0:0:0:0:0:1]:9", "Origin": "http://[::1]"},
        "10.1.1.1",
    ) is True
    assert allow(
        "POST",
        {"Host": "LOCALHOST:9", "Origin": "http://LocalHost:9"},
        "10.1.1.1",
    ) is True


def test_explicit_port_conflict_fails_closed():
    assert allow(
        "POST",
        {"Host": "127.0.0.1:9", "Origin": "http://127.0.0.1:8443"},
        "127.0.0.1",
    ) is False


def test_omitted_port_keeps_loopback_alias_contract():
    assert allow("POST", {"Host": "localhost:9", "Origin": "http://127.0.0.1"}, "10.1.1.1") is True
    assert allow("POST", {"Host": "127.0.0.1:80", "Origin": "http://127.0.0.1"}, "10.1.1.1") is True
    assert allow("POST", {"Host": "127.0.0.1", "Origin": "http://127.0.0.1:80"}, "10.1.1.1") is True


def test_userinfo_and_invalid_authority_fail_closed():
    assert allow(
        "POST",
        {"Host": "127.0.0.1:9", "Origin": "http://user:pass@127.0.0.1:9"},
        "127.0.0.1",
    ) is False
    assert allow(
        "POST",
        {"Host": "127.0.0.1:9", "Origin": "http://127.0.0.1:9@evil.example"},
        "127.0.0.1",
    ) is False
    assert allow("POST", {"Host": "user@127.0.0.1", "Origin": "http://127.0.0.1"}, "127.0.0.1") is False
    assert allow("POST", {"Host": "[::1", "Origin": "http://[::1]"}, "127.0.0.1") is False
    assert allow("POST", {"Host": "::1", "Origin": "http://[::1]"}, "127.0.0.1") is False
    assert allow("POST", {"Host": "127.0.0.1:99999", "Origin": "http://127.0.0.1"}, "127.0.0.1") is False
    assert allow("POST", {"Host": "127.0.0.1:9,evil.example", "Origin": "http://127.0.0.1:9"}, "127.0.0.1") is False


def test_repeated_headers_and_origin_referer_conflict_fail_closed():
    class Multi:
        def __init__(self, rows: list[tuple[str, str]]) -> None:
            self.rows = rows

        def getall(self, name: str, default: list[str] | None = None) -> list[str]:
            found = [value for key, value in self.rows if key.lower() == name.lower()]
            return found if found else ([] if default is not None else [])

    repeated = Multi([
        ("Host", "127.0.0.1:9"),
        ("Origin", "http://127.0.0.1:9"),
        ("Origin", "http://evil.example"),
    ])
    assert origin_allowed("POST", repeated, "127.0.0.1") is False
    assert allow(
        "POST",
        {"Host": "127.0.0.1:9", "host": "evil.example", "Origin": "http://127.0.0.1:9"},
        "127.0.0.1",
    ) is False
    assert allow(
        "POST",
        {
            "Host": "127.0.0.1:9",
            "Origin": "http://127.0.0.1:9",
            "Referer": "http://evil.example/panel",
        },
        "127.0.0.1",
    ) is False


def test_fetch_site_conflict_and_loopback_omit():
    assert allow(
        "GET",
        {"Host": "127.0.0.1:9", "Origin": "http://127.0.0.1:9", "Sec-Fetch-Site": "cross-site"},
        "127.0.0.1",
    ) is False
    assert allow(
        "POST",
        {"Host": "127.0.0.1:9", "Origin": "http://localhost:9", "Sec-Fetch-Site": "same-origin"},
        "127.0.0.1",
    ) is False
    assert allow("POST", {"Host": "127.0.0.1:9", "Sec-Fetch-Site": "same-origin"}, "127.0.0.1") is False
    assert allow("POST", {"Host": "127.0.0.1:9"}, "::1") is True
    assert allow("POST", {"Host": "127.0.0.1:9"}, "0:0:0:0:0:0:0:1") is True
    assert allow("POST", {"Host": "127.0.0.1:9"}, "127.0.0.2") is False
    assert allow("GET", {"Host": "127.0.0.1:9", "Referer": "http://evil.example/"}, "10.1.1.1") is True
    assert allow("POST", {"Host": "127.0.0.1:9", "Origin": "null"}, "127.0.0.1") is False
    assert allow("POST", {"Host": "127.0.0.1:9", "Origin": "http://[::1]"}, "127.0.0.1") is False


def test_artifact_root_format_follows_resolved_home_only(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "uploads").symlink_to(outside, target_is_directory=True)
    roots = _artifact_roots(home)
    resolved = str(home.resolve())
    assert [item[len(resolved):] for item in roots] == [
        "/uploads/",
        "/workspace/",
        "/images/",
        "/cron/output/",
    ]
    assert roots[0] == resolved + "/uploads/"
    assert not roots[0].startswith(str(outside.resolve()) + "/")

    real = tmp_path / "real-home"
    real.mkdir()
    link = tmp_path / "link-home"
    link.symlink_to(real, target_is_directory=True)
    linked = _artifact_roots(link)
    real_text = str(real.resolve())
    assert [item[len(real_text):] for item in linked] == [
        "/uploads/",
        "/workspace/",
        "/images/",
        "/cron/output/",
    ]
    assert all(not item.startswith(str(link) + "/") for item in linked)
    assert _artifact_roots(Path("not-absolute")) == []
    assert _artifact_roots(Path("/root/.hermes")) == []
    assert "/root/hermes-uploads/" not in _artifact_roots(Path("not-absolute"))
