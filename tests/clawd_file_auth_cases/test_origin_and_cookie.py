"""Origin, Referer, Host, Sec-Fetch-Site, and renew cookie flags."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.clawd_file_auth_cases.helpers import cookie_header, panel_request, require_server


def origin(method: str, headers: dict[str, str], peer: str | None) -> bool:
    from clawd_server.auth import origin_allowed

    return origin_allowed(method, headers, peer)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_referer_must_match_host_when_origin_is_absent(method: str):
    host = {"Host": "127.0.0.1:9", "Referer": "http://127.0.0.1:9/panel?x=1"}
    assert origin(method, host, "10.1.1.1") is True
    mismatch = {"Host": "127.0.0.1:9", "Referer": "http://evil.example/panel"}
    assert origin(method, mismatch, "127.0.0.1") is False


def test_get_ignores_bad_referer_but_not_cross_site_fetch():
    assert origin("GET", {"Host": "127.0.0.1:9", "Referer": "http://evil.example/"}, "10.1.1.1") is True
    assert origin(
        "GET",
        {"Host": "127.0.0.1:9", "Origin": "http://127.0.0.1:9", "Sec-Fetch-Site": "cross-site"},
        "127.0.0.1",
    ) is False


@pytest.mark.parametrize("site", ["same-origin", "same-site", "none"])
def test_allowed_fetch_site_still_requires_origin_match(site: str):
    good = {"Host": "127.0.0.1:9", "Origin": "http://127.0.0.1:9", "Sec-Fetch-Site": site}
    bad = {"Host": "127.0.0.1:9", "Origin": "http://evil.example", "Sec-Fetch-Site": site}
    assert origin("POST", good, "10.1.1.1") is True
    assert origin("POST", bad, "127.0.0.1") is False


def test_host_alias_and_null_origin():
    assert origin("POST", {"Host": "127.0.0.1:9", "Origin": "http://localhost:9"}, "10.1.1.1") is True
    assert origin("POST", {"Host": "localhost:9", "Origin": "http://127.0.0.1"}, "10.1.1.1") is True
    assert origin("POST", {"Host": "127.0.0.1:9", "Origin": "null"}, "127.0.0.1") is False
    assert origin("POST", {"Host": "evil.example", "Origin": "http://127.0.0.1:9"}, "127.0.0.1") is False
    assert origin("POST", {"Host": "127.0.0.1:9"}, "::1") is True


@pytest.mark.parametrize("host", ["[::1]", "[::1]:9"])
def test_bracketed_ipv6_host_matches_origin(host: str):
    headers = {"Host": host, "Origin": "http://[::1]", "Referer": "http://[::1]/panel"}
    assert origin("POST", headers, "10.1.1.1") is True


def _server(tmp_path: Path):
    from clawd_server.auth import add_user, credential_hash

    home = tmp_path / "home"
    home.mkdir()
    add_user(home, "ops", "secret")
    digest = credential_hash("ops", "secret")
    server = require_server()(home, None)
    return server, digest


def _renew(server, digest: str, *, scheme: str, headers: dict[str, str] | None = None):
    merged = {
        "Host": "127.0.0.1:9",
        "Origin": "http://127.0.0.1:9",
        "Cookie": f"hp_auth={digest}",
    }
    if headers:
        merged.update(headers)
    request = panel_request("POST", "/panel-auth/renew", merged, scheme=scheme, peer="127.0.0.1")
    return asyncio.run(server._renew(request))


def test_http_renew_sets_httponly_samesite_without_secure(tmp_path: Path):
    server, digest = _server(tmp_path)
    response = _renew(server, digest, scheme="http")
    assert response.status == 204
    header = cookie_header(response)
    assert "HttpOnly" in header
    assert "SameSite=Strict" in header
    assert "Max-Age=2592000" in header
    assert "Path=/" in header
    assert "Secure" not in header
    assert digest in header
    assert "Lax" not in header


def test_https_renew_sets_secure(tmp_path: Path):
    server, digest = _server(tmp_path)
    response = _renew(server, digest, scheme="https")
    assert response.status == 204
    header = cookie_header(response)
    assert "Secure" in header
    assert "HttpOnly" in header
    assert "SameSite=Strict" in header
    assert digest in header


def test_rejected_renew_does_not_set_cookie(tmp_path: Path):
    server, digest = _server(tmp_path)
    cross = _renew(server, digest, scheme="http", headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status == 403
    assert cross.status != 401
    assert "Set-Cookie" not in cross.headers
    assert json.loads(cross.body)["error"]["code"] == "origin_rejected"
    missing = _renew(server, "0" * 64, scheme="http")
    assert missing.status == 401
    assert "Set-Cookie" not in missing.headers
