"""未运行契约：真实 ``_handle_run_events`` 逐帧写出。

传输用记录型 ``StreamResponse``，不调用 ``listen`` / ``TestServer``，不访问模型。
所有权布尔是夹具；404 信封、游标、回放和写出循环是现有实现。
夹具不制造 ``run.completed`` 来证明回合成功。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager, suppress
from typing import Any, Iterator, cast

import pytest

from gateway.platforms import api_server as api_server_module
from gateway.platforms.api_server import APIServerAdapter
from gateway.platforms.api_server_runs import (
    _handle_run_events,
    _release_run_owner_if_forgotten,
)

pytestmark = pytest.mark.asyncio


def _web():
    from gateway.platforms import api_server_runs

    assert api_server_runs.web is not None, "aiohttp is required; this case does not install it"
    return api_server_runs


class _Request:
    """Handler reads only these three fields today. Do not invent more."""

    def __init__(self, run_id: str, *, header: str | None = None, query: dict[str, str] | None = None) -> None:
        self.match_info = {"run_id": run_id}
        self.headers = {} if header is None else {"Last-Event-ID": header}
        self.query = {} if query is None else dict(query)


class _Surface:
    def __init__(self) -> None:
        self._run_streams: dict[str, Any] = {}
        self._run_statuses: dict[str, Any] = {}
        self._active_run_agents: dict[str, Any] = {}
        self._active_run_tasks: dict[str, Any] = {}
        self._run_approval_sessions: dict[str, Any] = {}
        self._run_owners: dict[str, str] = {}
        self._cors_origins: tuple[str, ...] = ()
        self.owned = True

    def _check_run_auth(self, request: _Request, *, permission: str):
        assert permission == "status"
        assert request is not None
        return None

    def _request_owns_run(self, request: _Request, run_id: str) -> bool:
        assert request.match_info["run_id"] == run_id
        return self.owned

    def _cors_headers_for_origin(self, origin: str):
        return APIServerAdapter._cors_headers_for_origin(cast(Any, self), origin)

    def _sse_headers(self, request: _Request, extra: dict[str, str] | None = None):
        return APIServerAdapter._sse_headers(cast(Any, self), request, extra=extra)

    def _release_run_owner_if_forgotten(self, run_id: str) -> None:
        _release_run_owner_if_forgotten(self, run_id)


def _install_recorder(runs_module):
    class Recording(runs_module.web.StreamResponse):
        hold_first_data = False
        hold: asyncio.Event | None = None
        entered: asyncio.Event | None = None
        latest: Recording | None = None

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.chunks: list[bytes] = []
            self.prepared = False
            self.force_closed = False
            self.held = False
            Recording.latest = self

        async def prepare(self, request: Any) -> None:
            self.prepared = True
            self.prepare_request = request

        async def write(self, data: bytes) -> None:
            if not self.prepared:
                raise AssertionError("event bytes were written before the stream head")
            blob = bytes(data)
            self.chunks.append(blob)
            entered = type(self).entered
            hold = type(self).hold
            if type(self).hold_first_data and not self.held and b"data: " in blob and entered is not None and hold is not None:
                self.held = True
                entered.set()
                await hold.wait()

        def force_close(self) -> None:
            self.force_closed = True

    return Recording


@contextmanager
def _patched_response(runs_module) -> Iterator[type]:
    recording = _install_recorder(runs_module)
    original = runs_module.web.StreamResponse
    runs_module.web.StreamResponse = recording
    try:
        yield recording
    finally:
        runs_module.web.StreamResponse = original
        recording.hold_first_data = False
        recording.hold = None
        recording.entered = None
        recording.latest = None


def _payloads(chunks: list[bytes]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for block in b"".join(chunks).decode().split("\n\n"):
        data = [line.removeprefix("data: ") for line in block.splitlines() if line.startswith("data: ")]
        if data:
            payloads.append(json.loads("\n".join(data)))
    return payloads


def _owned(run_id: str):
    from gateway.platforms.api_server_runs import _RunStream

    surface = _Surface()
    stream = _RunStream()
    surface._run_streams[run_id] = stream
    surface._run_owners[run_id] = "owner"
    return surface, stream


async def test_first_frame_is_written_before_the_rest_of_the_turn() -> None:
    runs = _web()
    surface, stream = _owned("run-live")
    stream.put_nowait({"event": "message.delta", "delta": "first-delta", "run_id": "run-live"})
    with _patched_response(runs) as recording:
        recording.hold_first_data = True
        recording.hold = asyncio.Event()
        recording.entered = asyncio.Event()
        task = asyncio.create_task(
            _handle_run_events(surface, _Request("run-live", query={"last_seq": "-1"}), _api_server=api_server_module)
        )
        await asyncio.wait_for(recording.entered.wait(), timeout=2)
        recorder = recording.latest
        assert recorder is not None
        assert recorder.chunks[0] == b": open\n\n"
        assert recorder.headers["Content-Type"] == "text/event-stream"
        assert recorder.headers["Cache-Control"] == "no-cache"
        assert recorder.headers["X-Accel-Buffering"] == "no"
        first = recorder.chunks[1]
        assert b"first-delta" in first
        assert b"second-delta" not in first
        stream.put_nowait({"event": "message.delta", "delta": "second-delta", "run_id": "run-live"})
        assert all(b"second-delta" not in chunk for chunk in recorder.chunks)
        assert b"run.completed" not in b"".join(recorder.chunks)
        recording.hold.set()
        await asyncio.wait_for(_until(lambda: b"second-delta" in b"".join(recorder.chunks)), timeout=2)
        assert b"second-delta" not in first
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert stream.terminal is False
    assert not any(item.get("event") == "run.completed" for item in _payloads(recorder.chunks))
    assert recorder.prepare_request is not None
    assert stream.subscribers == set()


async def test_header_cursor_beats_query_and_skips_seen_seq() -> None:
    runs = _web()
    surface, stream = _owned("run-cursor")
    stream.put_nowait({"event": "message.delta", "delta": "seen"})
    stream.put_nowait({"event": "message.delta", "delta": "missed"})
    stream.put_nowait(None)
    with _patched_response(runs) as recording:
        response = await _handle_run_events(
            surface,
            _Request("run-cursor", header="0", query={"last_seq": "-1"}),
            _api_server=api_server_module,
        )
        assert response.status == 200
        assert recording.latest is not None
        chunks = list(recording.latest.chunks)
    payloads = _payloads(chunks)
    assert [item["delta"] for item in payloads] == ["missed"]
    assert b": stream closed\n\n" in b"".join(chunks)
    assert not any(item.get("event") == "run.completed" for item in payloads)
    assert stream.terminal is True


async def test_invalid_and_negative_cursors_fall_back_without_marking_completed() -> None:
    runs = _web()
    surface, stream = _owned("run-bad-cursor")
    stream.put_nowait({"event": "message.delta", "delta": "zero"})
    stream.put_nowait(None)
    with _patched_response(runs) as recording:
        await _handle_run_events(
            surface,
            _Request("run-bad-cursor", query={"last_seq": "nope"}),
            _api_server=api_server_module,
        )
        invalid_payloads = _payloads(recording.latest.chunks)
    assert [item["delta"] for item in invalid_payloads] == ["zero"]
    surface, stream = _owned("run-negative")
    stream.put_nowait({"event": "message.delta", "delta": "zero"})
    stream.put_nowait(None)
    with _patched_response(runs) as recording:
        await _handle_run_events(
            surface,
            _Request("run-negative", query={"last_seq": "-9"}),
            _api_server=api_server_module,
        )
        negative_payloads = _payloads(recording.latest.chunks)
    assert [item["seq"] for item in negative_payloads] == [0]
    assert stream.terminal is True


async def test_truncated_replay_is_a_frame_not_a_completed_body() -> None:
    runs = _web()
    surface, stream = _owned("run-gap")
    for index in range(stream.BACKLOG_LIMIT + 1):
        stream.put_nowait({"event": "message.delta", "delta": str(index)})
    with _patched_response(runs) as recording:
        recording.hold_first_data = True
        recording.hold = asyncio.Event()
        recording.entered = asyncio.Event()
        task = asyncio.create_task(
            _handle_run_events(surface, _Request("run-gap", query={"last_seq": "-1"}), _api_server=api_server_module)
        )
        await asyncio.wait_for(recording.entered.wait(), timeout=2)
        payloads = _payloads(recording.latest.chunks)
        assert payloads[0]["event"] == "replay.truncated"
        assert payloads[0]["oldest_retained_seq"] == 1
        assert payloads[0]["requested_seq"] == -1
        assert payloads[0]["event"] != "run.completed"
        assert b"run.completed" not in recording.latest.chunks[1]
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert stream.subscribers == set()


async def test_terminal_replay_closes_without_synthesizing_completed() -> None:
    runs = _web()
    surface, stream = _owned("run-terminal")
    stream.put_nowait({"event": "message.delta", "delta": "before"})
    stream.put_nowait(None)
    stream.put_nowait({"event": "run.completed"})
    with _patched_response(runs) as recording:
        await _handle_run_events(
            surface,
            _Request("run-terminal", header="0"),
            _api_server=api_server_module,
        )
        body = b"".join(recording.latest.chunks)
        payloads = _payloads(recording.latest.chunks)
    assert payloads == []
    assert b": stream closed\n\n" in body
    assert b"run.completed" not in body
    assert stream.next_seq == 2


async def test_other_run_events_do_not_enter_this_response() -> None:
    runs = _web()
    surface, left = _owned("run-a")
    right = type(left)()
    surface._run_streams["run-b"] = right
    left.put_nowait({"event": "message.delta", "delta": "from-a"})
    right.put_nowait({"event": "message.delta", "delta": "from-b"})
    right.put_nowait({"event": "run.completed"})
    left.put_nowait(None)
    with _patched_response(runs) as recording:
        await _handle_run_events(surface, _Request("run-a"), _api_server=api_server_module)
        payloads = _payloads(recording.latest.chunks)
    assert [item["delta"] for item in payloads] == ["from-a"]
    assert all(item.get("event") != "run.completed" for item in payloads)


async def test_unowned_run_is_rejected_without_attaching_or_completing() -> None:
    runs = _web()
    surface, stream = _owned("run-foreign")
    surface.owned = False
    stream.put_nowait({"event": "message.delta", "delta": "secret"})
    with _patched_response(runs) as recording:
        response = await _handle_run_events(surface, _Request("run-foreign"), _api_server=api_server_module)
        assert recording.latest is None
    assert response.status == 404
    assert _error_code(response) == "run_not_found"
    assert stream.subscribers == set()
    assert stream.terminal is False
    assert surface._run_statuses.get("run-foreign", {}).get("status") != "completed"


def _error_code(response) -> str:
    raw = response.body
    assert isinstance(raw, (bytes, bytearray, memoryview)), type(raw).__name__
    body = json.loads(bytes(raw).decode())
    code = body["error"]["code"]
    assert isinstance(code, str)
    return code


async def _until(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0)
