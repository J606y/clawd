"""Exercise the real history HTTP handler against isolated SessionDB rows."""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_state import SessionDB

@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["plain", "compressed-with-child", "main-continuation", "child-continuation"])
async def test_real_history_http_preserves_selected_lineage(tmp_path, mode):
    db = SessionDB(db_path=tmp_path / "state.db")
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "isolated-history-test", "host": "127.0.0.1", "port": 0}))
    adapter._session_db = db
    adapter._check_auth = lambda _request: None
    def make(sid, source="api_server", parent=None, text=""):
        db.create_session(sid, source=source, parent_session_id=parent)
        db.append_message(sid, role="assistant", content=text)
    make("main", text="MAIN_KEEP")
    if mode in {"compressed-with-child", "main-continuation"}:
        db.end_session("main", "compression")
    if mode == "main-continuation":
        make("main-tip", parent="main", text="MAIN_CONTINUED")
    make("child", source="subagent", parent="main", text="CHILD_ONLY")
    if mode == "child-continuation":
        db.end_session("child", "compression")
        make("child-tip", source="subagent", parent="child", text="CHILD_CONTINUED")
    app = web.Application()
    app.router.add_get("/api/sessions/{session_id}/messages", adapter._handle_session_messages)
    try:
        async with TestClient(TestServer(app)) as client:
            for _ in range(2):
                response = await client.get("/api/sessions/main/messages?limit=100&offset=0")
                assert response.status == 200
                body = await response.json()
                assert body["session_id"] == ("main-tip" if mode == "main-continuation" else "main")
                texts = [row["content"] for row in body["data"]]
                assert "MAIN_KEEP" in texts
                assert "CHILD_ONLY" not in texts
                assert "CHILD_CONTINUED" not in texts
                if mode == "main-continuation":
                    assert "MAIN_CONTINUED" in texts
            response = await client.get("/api/sessions/child/messages?limit=100&offset=0")
            assert response.status == 200
            body = await response.json()
            assert body["session_id"] == ("child-tip" if mode == "child-continuation" else "child")
            assert ("CHILD_CONTINUED" if mode == "child-continuation" else "CHILD_ONLY") in [row["content"] for row in body["data"]]
            response = await client.get("/api/sessions/missing/messages")
            assert response.status == 404
            assert db.get_session("main")["source"] == "api_server"
            assert db.get_session("child")["source"] == "subagent"
    finally:
        db.close()
