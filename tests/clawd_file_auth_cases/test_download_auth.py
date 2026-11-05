"""Download requires panel cookie auth before sidecar bytes, then the sidecar key."""

from __future__ import annotations

import json
from pathlib import Path

from tests.clawd_file_auth_cases.helpers import (
    dispatch,
    isolated_sidecar,
    panel_request,
    require_server,
)

PAYLOAD = b"DOWNLOAD-PRIVATE"


def _auth(home: Path):
    from clawd_server.auth import add_user, credential_hash

    add_user(home, "ops", "secret")
    return credential_hash("ops", "secret")


def test_panel_download_without_cookie_is_401_and_does_not_call_upstream(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    PanelServer = require_server()
    server = PanelServer(home, None)

    class Tripwire:
        async def request(self, *_args, **_kwargs):
            raise AssertionError("upstream called without panel auth")

    server.client = Tripwire()
    request = panel_request(
        "GET",
        "/panel-sidecar/files/download?root=uploads&path=secret.txt",
        {"Host": "127.0.0.1:9", "Origin": "http://127.0.0.1:9"},
        match_info={"tail": "files/download"},
        peer="127.0.0.1",
    )

    async def call():
        return await server._proxy_sidecar(request)

    import asyncio

    response = asyncio.run(call())
    assert response.status == 401
    body = json.loads(response.body)
    assert body["error"]["code"] == "unauthorized"
    assert PAYLOAD not in response.body


def test_panel_download_bad_origin_is_403_not_401(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    digest = _auth(home)
    PanelServer = require_server()
    server = PanelServer(home, None)
    request = panel_request(
        "GET",
        "/panel-sidecar/files/download?root=uploads&path=secret.txt",
        {
            "Host": "127.0.0.1:9",
            "Origin": "http://evil.example",
            "Cookie": f"hp_auth={digest}",
        },
        peer="10.1.1.1",
    )

    import asyncio

    response = asyncio.run(server._authed(request))
    assert response is not None
    assert response.status == 403
    assert response.status != 401
    assert json.loads(response.body)["error"]["code"] == "origin_rejected"


def test_sidecar_download_requires_key_and_serves_in_root_bytes(tmp_path: Path):
    with isolated_sidecar(tmp_path) as (home, mod):
        (home / "uploads" / "secret.txt").write_bytes(PAYLOAD)
        missing, _headers, body = dispatch(
            mod, "GET", "/files/download?root=uploads&path=secret.txt", key=""
        )
        assert missing == 401
        assert PAYLOAD not in body
        wrong, _headers, body = dispatch(
            mod, "GET", "/files/download?root=uploads&path=secret.txt", key="wrong"
        )
        assert wrong == 401
        assert PAYLOAD not in body
        status, headers, body = dispatch(
            mod, "GET", "/files/download?root=uploads&path=secret.txt", key="k"
        )
        assert status == 200, body
        assert body == PAYLOAD
        assert headers["cache-control"] == "no-store"
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"OUTSIDE")
        (home / "uploads" / "escape.txt").symlink_to(outside)
        denied, _headers, body = dispatch(
            mod, "GET", "/files/download?root=uploads&path=escape.txt", key="k"
        )
        assert denied in {400, 403}
        assert denied != 401
        assert b"OUTSIDE" not in body


def test_valid_key_does_not_bypass_read_only_or_deny(tmp_path: Path):
    with isolated_sidecar(tmp_path) as (home, mod):
        (home / "uploads" / "id_rsa").write_bytes(PAYLOAD)
        status, _headers, body = dispatch(
            mod, "GET", "/files/download?root=uploads&path=id_rsa", key="k"
        )
        assert status == 403
        assert PAYLOAD not in body
        denied = dispatch(mod, "POST", "/files/upload?root=images&name=a.txt", key="k", body=b"x")
        assert denied[0] == 403
        assert denied[0] != 401
