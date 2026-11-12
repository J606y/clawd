"""Browser-origin and internal proxy regression coverage."""
import asyncio
from types import SimpleNamespace

import pytest

from clawd_server.auth import origin_allowed
from clawd_server.server import PanelServer


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_browser_read_without_origin(method):
    headers = {"Host": "hermes.20051212.xyz", "Referer": "https://hermes.20051212.xyz/", "Sec-Fetch-Site": "same-origin"}
    assert origin_allowed(method, headers, "127.0.0.1")
    headers["Referer"] = "https://evil.example/"
    assert not origin_allowed(method, headers, "127.0.0.1")


@pytest.mark.parametrize("change", [{"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}, {"Referer": "https://evil.example/"}])
def test_cross_site_remains_denied(change):
    headers = {"Host": "hermes.20051212.xyz", "Referer": "https://hermes.20051212.xyz/", "Sec-Fetch-Site": "same-origin"}
    headers.update(change)
    assert not origin_allowed("GET", headers, "127.0.0.1")


def test_write_still_requires_origin():
    assert not origin_allowed("POST", {"Host": "hermes.20051212.xyz", "Referer": "https://hermes.20051212.xyz/", "Sec-Fetch-Site": "same-origin"}, "127.0.0.1")


@pytest.mark.parametrize("inject", [{"Authorization": "Bearer internal-test"}, {"X-Panel-Key": "internal-test"}])
def test_internal_proxy_strips_browser_credentials_and_origin(inject):
    observed = {}

    async def upstream(method, url, **kwargs):
        observed.update(kwargs["headers"])
        assert url == "http://127.0.0.1:1/health"
        raise OSError("intentional test connection failure")

    async def read():
        return b""

    request = SimpleNamespace(match_info={"tail": "health"}, query_string="", method="GET", read=read, headers={
        "Host": "hermes.20051212.xyz", "Origin": "https://hermes.20051212.xyz", "Referer": "https://hermes.20051212.xyz/", "Cookie": "browser-test", "Authorization": "Bearer browser-test", "X-Panel-Key": "browser-test", "Sec-Fetch-Site": "same-origin", "Accept": "application/json",
    })
    panel = SimpleNamespace(client=SimpleNamespace(request=upstream))
    response = asyncio.run(PanelServer._proxy(panel, request, "http://127.0.0.1:1", inject))
    assert response.status == 503
    assert observed == {"Accept": "application/json", **inject}
