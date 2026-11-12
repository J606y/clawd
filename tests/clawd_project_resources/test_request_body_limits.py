"""Upload body allowance is scoped to the registered multipart project route."""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gateway.platforms.api_server import MAX_REQUEST_BYTES, body_limit_middleware
from clawd_projects.resources import FILE_BYTE_LIMIT


@pytest.mark.asyncio
async def test_only_registered_multipart_file_routes_accept_more_than_json_limit():
    async def size(request):
        body = await request.read()
        return web.json_response({"size": len(body)})
    app = web.Application(middlewares=[body_limit_middleware], client_max_size=MAX_REQUEST_BYTES)
    app.router.add_post("/api/projects/{id}/resources/files", size)
    app.router.add_post("/p/{profile}/api/projects/{id}/resources/files", size)
    app.router.add_post("/api/other", size)
    payload = b"x" * (MAX_REQUEST_BYTES + 1)
    async with TestClient(TestServer(app)) as client:
        for path in ("/api/projects/p1/resources/files", "/p/named/api/projects/p1/resources/files"):
            response = await client.post(path, data=payload, headers={"Content-Type": "multipart/form-data; boundary=test"})
            assert response.status == 200
            assert (await response.json())["size"] == len(payload)
        for path, content_type in (("/api/other", "multipart/form-data; boundary=test"), ("/api/projects/p1/resources/files", "application/json")):
            response = await client.post(path, data=payload, headers={"Content-Type": content_type})
            assert response.status == 413
            assert (await response.json())["error"]["code"] == "body_too_large"


@pytest.mark.asyncio
async def test_project_multipart_limit_is_still_bounded():
    async def size(request):
        return web.json_response({"size": len(await request.read())})
    app = web.Application(middlewares=[body_limit_middleware], client_max_size=MAX_REQUEST_BYTES)
    app.router.add_post("/api/projects/{id}/resources/files", size)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/projects/p1/resources/files", data=b"x" * (FILE_BYTE_LIMIT + 16 * 1024 + 1), headers={"Content-Type": "multipart/form-data; boundary=test"})
        assert response.status == 413
        assert (await response.json())["error"]["code"] == "body_too_large"
