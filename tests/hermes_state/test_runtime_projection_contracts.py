"""Static contracts for runtime execution, last-call snapshot, and profile store binding.

Not executed in this implementation pass.
"""

from __future__ import annotations

from hermes_state_last_call import normalize_last_call
from clawd_server.runtime_native import bind_run_store, store_for_db


class _Store:
    def __init__(self, path):
        self._db_path = path


class _DB:
    def __init__(self, path):
        self.db_path = path


def test_run_store_is_scoped_to_the_session_home(tmp_path):
    first = tmp_path / "alpha" / "runs_idempotency.db"
    second = tmp_path / "beta" / "runs_idempotency.db"
    first.parent.mkdir()
    second.parent.mkdir()
    bind_run_store(_Store(str(first)))
    bind_run_store(_Store(str(second)))
    assert store_for_db(_DB(tmp_path / "alpha" / "state.db")) is not store_for_db(_DB(tmp_path / "beta" / "state.db"))
    assert store_for_db(_DB(tmp_path / "gamma" / "state.db")) is None


def test_last_call_snapshot_rejects_a_missing_prompt():
    assert normalize_last_call(None) is None
    try:
        normalize_last_call({"output_tokens": 1})
    except ValueError:
        return
    raise AssertionError("prompt_tokens is required")
