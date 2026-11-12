"Session resume must not mistake unmarked delegated work for the main thread."
import pytest
from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    store = SessionDB(db_path=tmp_path / "state.db")
    try:
        yield store
    finally:
        store.close()


def make(store, sid, parent=None, source="api_server", text=None):
    store.create_session(sid, source=source, parent_session_id=parent)
    if text is not None:
        store.append_message(sid, role="assistant", content=text)


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("parent_has_messages", [False, True])
def test_unmarked_subagent_cannot_take_over_main(db, compressed, parent_has_messages):
    make(db, "main", text="main history" if parent_has_messages else None)
    if compressed:
        db.end_session("main", "compression")
    make(db, "delegate", "main", "subagent", "delegated result")
    assert db.get_compression_tip("main") == "main"
    assert db.resolve_resume_session_id("main") == "main"
    assert db.resolve_resume_session_id("delegate") == "delegate"
    assert db.get_session("main")["source"] == "api_server"
    assert db.get_session("delegate")["parent_session_id"] == "main"


def test_real_continuation_wins_over_newer_unmarked_delegate(db):
    make(db, "main", text="old main")
    db.end_session("main", "compression")
    make(db, "continuation", "main", text="new main")
    make(db, "delegate", "main", "subagent", "child work")
    db._execute_write(lambda conn: conn.execute(
        "UPDATE sessions SET started_at = started_at + 1000 WHERE id = ?", ("delegate",)))
    assert db.get_compression_tip("main") == "continuation"
    assert db.resolve_resume_session_id("main") == "continuation"


def test_subagent_own_compression_is_preserved(db):
    make(db, "main", text="main history")
    make(db, "delegate", "main", "subagent", "before compression")
    db.end_session("delegate", "compression")
    make(db, "delegate-tip", "delegate", "subagent", "after compression")
    assert db.get_compression_tip("delegate") == "delegate-tip"
    assert db.resolve_resume_session_id("delegate") == "delegate-tip"
    assert db.resolve_resume_session_id("main") == "main"
