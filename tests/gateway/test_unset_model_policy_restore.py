"""An unset launch model is a restorable session, not storage corruption.

Missing model credentials are not the refusal. ``stage_local_session`` persists
JSON null when the profile names no model; ``restore_policy`` must accept that
and must still refuse a present non-string or blank model.
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

from gateway.session_policy import build_policy, restore_policy
from hermes_state_runtime import RuntimeStoreError


def test_restore_accepts_explicit_unset_model_and_rejects_a_present_bad_one(tmp_path):
    policy = build_policy({"source": "tui", "cwd": str(tmp_path), "toolsets": []}, {})
    assert policy.model is None
    restored = restore_policy(asdict(policy))
    assert restored.model is None
    assert restored.source == "tui"
    assert restored.cwd == str(tmp_path)

    missing = dict(asdict(policy))
    missing.pop("model")
    with pytest.raises(RuntimeStoreError) as missing_exc:
        restore_policy(missing)
    assert missing_exc.value.reason == "storage_unavailable"

    for bad in (1, "  ", ""):
        payload = dict(asdict(policy))
        payload["model"] = bad
        with pytest.raises(RuntimeStoreError) as exc:
            restore_policy(payload)
        assert exc.value.reason == "storage_unavailable"


@pytest.mark.asyncio
async def test_create_without_a_profile_model_persists_and_restores(tmp_path, monkeypatch):
    """The TUI create path that logged invalid policy at restore_policy:1007."""
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_local import create_local_session
    from hermes_state_local import local_receipt

    monkeypatch.setattr(run, "_load_gateway_config", lambda *args, **kwargs: {})
    monkeypatch.setattr("hermes_cli.anon_auth.trial_model", lambda: "")
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    runner = type("Runner", (), {
        "session_store": store,
        "_session_db": store._db,
        "adapters": {},
        "_draining": False,
        "_cached_agent_for": lambda self, route: None,
        "_adapter_for_source": lambda self, source: None,
        "_evict_cached_agent": lambda self, route: None,
    })()
    authority = await initialize_session_authority(
        runner, profile_id=str(tmp_path), instance_id="unset-model")
    connection = AuthorityConnection(authority, object(), {"user_id": "owner"})
    try:
        ref = create_local_session(authority, connection.actor, {
            "request_id": "tui-unset", "source": "tui", "cwd": str(tmp_path), "toolsets": [],
        })
        receipt = local_receipt(authority.db, ref.session_id)
        policy = restore_policy(receipt["policy"])
        row = authority.db.get_session(ref.session_id)
    finally:
        await connection.close()
        authority.db.close()

    assert policy.model is None
    assert row is not None
    assert row["id"] == ref.session_id
    assert authority.sessions[ref.session_id].source is not None
