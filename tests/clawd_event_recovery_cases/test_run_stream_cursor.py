"""未运行契约：真实 ``_RunStream`` 与 ``terminal_run_status``。

不监听端口，不调用模型，不把夹具响应当成回合成功。
审批与续跑竞态属于 ``tests/clawd_race_cases``，这里不覆盖。
"""

from __future__ import annotations

import pytest

from clawd_server.contract import SSE_CURSOR
from gateway.platforms.api_server_runs import (
    _RUN_STREAM_SUBSCRIBER_OVERFLOW,
    _RunStream,
    terminal_run_status,
)

pytestmark = pytest.mark.asyncio


async def test_frozen_cursor_name() -> None:
    """断线续传查询名以冻结契约为准，不另起一个游标字段。"""
    assert SSE_CURSOR == "last_seq"


def _body(item: tuple[int, dict | None]) -> dict:
    _seq, event = item
    assert event is not None
    return event


async def test_last_seq_replays_only_unseen_and_does_not_double_queue() -> None:
    stream = _RunStream()
    stream.put_nowait({"event": "message.delta", "delta": "seen"})
    stream.put_nowait({"event": "message.delta", "delta": "missed"})

    queue, replay = stream.attach(0)

    assert [seq for seq, _event in replay] == [1]
    assert _body(replay[0])["delta"] == "missed"
    assert queue.empty()
    stream.put_nowait({"event": "message.delta", "delta": "live"})
    seq, event = queue.get_nowait()
    assert event is not None
    assert seq == 2
    assert event["delta"] == "live"
    assert queue.empty()


async def test_default_cursor_and_negative_cursor_keep_seq_zero() -> None:
    stream = _RunStream()
    stream.put_nowait({"event": "message.delta", "delta": "zero"})
    _queue, default_replay = stream.attach()
    _again, negative_replay = stream.attach(-1)
    assert [seq for seq, _event in default_replay] == [0]
    assert [seq for seq, _event in negative_replay] == [0]


async def test_repeated_payload_gets_a_new_seq_but_seen_seq_is_not_replayed() -> None:
    """存储不按正文去重。幂等边界是 seq：已见游标不得再次回放。"""
    stream = _RunStream()
    payload = {"event": "message.delta", "delta": "same"}
    stream.put_nowait(payload)
    stream.put_nowait(dict(payload))
    _queue, first = stream.attach(-1)
    assert [item[0] for item in first] == [0, 1]
    assert first[0][1] is not first[1][1]
    _again, resumed = stream.attach(0)
    assert [item[0] for item in resumed] == [1]
    assert resumed[0][1] is not None
    assert _body(resumed[0])["delta"] == "same"


async def test_terminal_sentinel_drops_later_events_and_replays_in_order() -> None:
    stream = _RunStream()
    stream.put_nowait({"event": "message.delta", "delta": "before"})
    stream.put_nowait(None)
    frozen = stream.next_seq
    stream.put_nowait({"event": "run.completed", "completed": True})
    assert stream.terminal is True
    assert stream.next_seq == frozen
    _queue, replay = stream.attach(-1)
    assert [seq for seq, _event in replay] == [0, 1]
    assert replay[0][1] is not None
    assert _body(replay[0])["delta"] == "before"
    assert replay[1][1] is None
    assert all(event is None or event.get("event") != "run.completed" for _seq, event in replay)
    seqs = [seq for seq, _event in replay]
    assert seqs == sorted(seqs)


async def test_backlog_eviction_leaves_a_monotonic_gap() -> None:
    stream = _RunStream()
    for index in range(stream.BACKLOG_LIMIT + 1):
        stream.put_nowait({"event": "message.delta", "delta": str(index)})
    _queue, replay = stream.attach(-1)
    seqs = [seq for seq, _event in replay]
    assert seqs[0] == 1
    assert seqs == list(range(1, stream.BACKLOG_LIMIT + 1))
    assert replay[0][0] > -1 + 1


async def test_detach_does_not_terminalize_or_emit_completed() -> None:
    stream = _RunStream()
    left, _replay = stream.attach()
    right, _replay = stream.attach()
    stream.detach(left)
    stream.put_nowait({"event": "message.delta", "delta": "still-live"})
    assert left.empty()
    seq, event = right.get_nowait()
    assert event is not None
    assert seq == 0
    assert event["event"] == "message.delta"
    assert event.get("event") != "run.completed"
    assert stream.terminal is False
    assert left not in stream.subscribers
    assert right in stream.subscribers


async def test_separate_streams_do_not_cross_runs() -> None:
    first = _RunStream()
    second = _RunStream()
    first_queue, _replay = first.attach()
    second_queue, _replay = second.attach()
    first.put_nowait({"event": "message.delta", "delta": "only-first", "run_id": "run-a"})
    assert second_queue.empty()
    assert first_queue.get_nowait()[1]["delta"] == "only-first"
    assert second.terminal is False


async def test_slow_subscriber_is_detached_instead_of_buffering_the_turn() -> None:
    stream = _RunStream()
    queue, _replay = stream.attach()
    for index in range(stream.SUBSCRIBER_QUEUE_LIMIT):
        stream.put_nowait({"event": "message.delta", "delta": str(index)})
    assert queue.full()
    stream.put_nowait({"event": "run.completed", "completed": True})
    assert queue not in stream.subscribers
    seq, marker = queue.get_nowait()
    assert marker is _RUN_STREAM_SUBSCRIBER_OVERFLOW
    assert seq == stream.SUBSCRIBER_QUEUE_LIMIT
    assert queue.empty()
    assert stream.terminal is False
    _fresh, replay = stream.attach(stream.SUBSCRIBER_QUEUE_LIMIT - 1)
    assert replay[-1][1] is not None
    assert _body(replay[-1])["event"] == "run.completed"


@pytest.mark.parametrize(
    ("result", "status"),
    (
        ({"interrupted": True}, "cancelled"),
        ({"failed": True}, "failed"),
        ({"partial": True}, "failed"),
        ({"completed": False}, "failed"),
        ({"interrupted": True, "completed": True, "partial": True}, "cancelled"),
    ),
)
async def test_nonfinish_results_are_not_completed(result: dict, status: str) -> None:
    got, fields = terminal_run_status(result)
    assert got == status
    assert got != "completed"
    assert fields["completed"] is False


async def test_only_an_actual_finish_maps_to_completed() -> None:
    status, fields = terminal_run_status({"completed": True})
    assert status == "completed"
    assert fields["completed"] is True
    assert fields["interrupted"] is False
