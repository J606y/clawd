"""Core auto-continue after an API-session subagent completion.

Ownership lives here, started and stopped with the API server adapter. A panel,
sidecar poller, or extra web request is not required. Completion notices stay
delivery rows; this service may admit a follow-up only after code gates pass.

A new journal freeze is written only after a pending reasoning tier has been
committed on the real authority store, under keyed_turn and the live mutation
lock. The frozen body is the spec admit launches. An in-flight freeze whose
aliases are covered is not rewritten when a run is already reserved under it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Optional

from gateway.clawd_continuation_admit import (
    FROZEN_LAUNCH, ContinuationAdmitError, admit_continuation, continuation_reservation,
    refresh_continuation_spec,
)
from gateway.clawd_continuation_decision import BACKOFF_S, Decision, Snapshot, decide
from gateway.clawd_continuation_gates import collect_gates, latest_run_status, turn_lease_expires
from gateway.clawd_continuation_journal import ContinuationJournal, JournalUnavailable

logger = logging.getLogger(__name__)
_SERVICES: dict[int, "ContinuationService"] = {}
_LEASE_S = 30.0
# Terminal stops that consume the notice. A success streak is not one of them.
_RECEIPT_ON_BLOCK = frozenset({"attempts_exhausted", "cancelled"})
_ADMITTED_RECEIPT_REASON = "admitted"


def service_for(adapter: Any) -> Optional["ContinuationService"]:
    return _SERVICES.get(id(adapter))


def note_persisted_delivery(adapter: Any, session_id: str, evt: Optional[dict] = None) -> None:
    """Schedule evaluation after a delivery row is durable. Never starts a run itself."""
    service = service_for(adapter)
    if service is None or not service.running or not session_id:
        return
    db = _db(adapter)
    if db is None:
        service.fail_closed(session_id, "ownership_unconfirmed")
        return
    try:
        service.journal.watch(session_id, service._now() + 2 * 3600)
    except JournalUnavailable:
        service.fail_closed(session_id, "ownership_unconfirmed")
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        service.fail_closed(session_id, "ownership_unconfirmed")
        return
    service._spawn(service.evaluate(session_id, db, profile=_profile(adapter)))


def note_run_settled(adapter: Any, session_id: str) -> None:
    service = service_for(adapter)
    if service is None or not service.running or not session_id:
        return
    db = _db(adapter)
    if db is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    service._spawn(service.evaluate(session_id, db, profile=_profile(adapter)))


def read_status(adapter: Any, session_id: str) -> dict[str, Any]:
    """Unique status source. Read-only: does not evaluate and does not admit."""
    service = service_for(adapter)
    if service is not None:
        return service.status(session_id)
    path = _journal_path(adapter)
    if path is None or not path.exists():
        return {"session_id": session_id, "request": None, "last_event": None}
    journal = ContinuationJournal(path)
    try:
        journal.open()
        return journal.status(session_id)
    except JournalUnavailable as exc:
        return {"session_id": session_id, "request": None, "last_event": None, "error": str(exc)}
    finally:
        journal.close()


def arm_adapter(adapter: Any) -> None:
    if id(adapter) in _SERVICES:
        return
    path = _journal_path(adapter)
    if path is None:
        adapter._clawd_continuation_error = "ownership_unconfirmed"
        logger.error("clawd continuation not armed: session store unavailable")
        return
    service = ContinuationService(adapter, journal_path=path)
    try:
        service.open()
    except JournalUnavailable as exc:
        adapter._clawd_continuation_error = "ownership_unconfirmed"
        logger.error("clawd continuation not armed: %s", exc)
        return
    _SERVICES[id(adapter)] = service
    adapter._clawd_continuation = service
    adapter._clawd_continuation_error = None
    service.start()


def disarm_adapter(adapter: Any) -> None:
    service = _SERVICES.pop(id(adapter), None)
    adapter._clawd_continuation = None
    if service is not None:
        service.stop()


def runtime_status(adapter: Any) -> dict[str, Any]:
    """Live service state. A successful import is not a start."""
    service = service_for(adapter) if adapter is not None else None
    running = bool(service is not None and getattr(service, "running", False))
    error = getattr(adapter, "_clawd_continuation_error", None) if adapter is not None else "adapter_missing"
    return {
        "state": "started" if running else "not-started",
        "running": running,
        "error": None if running else error,
    }


def ready_fields(adapter: Any) -> dict[str, Any]:
    """Fields for /clawd/ready. ``continuation`` is live state, never module presence."""
    live = runtime_status(adapter)
    return {
        "continuation": live["state"],
        "continuationRunning": live["running"],
        "continuationError": live["error"],
    }


def _db(adapter: Any) -> Any:
    ensure = getattr(adapter, "_ensure_session_db", None)
    if not callable(ensure):
        return getattr(adapter, "_session_db", None)
    try:
        return ensure()
    except Exception:
        logger.exception("clawd continuation session store unavailable")
        return None


def _journal_path(adapter: Any) -> Optional[Path]:
    db = _db(adapter)
    path = getattr(db, "db_path", None) if db is not None else None
    if not path:
        return None
    return Path(path).parent / "clawd_continuation.db"


def _profile(adapter: Any) -> str:
    explicit = getattr(adapter, "clawd_profile", None)
    if explicit:
        return str(explicit)
    try:
        from gateway.platforms.api_server import _api_request_profile
        current = _api_request_profile.get()
    except Exception:
        current = None
    return str(current or "default")


def _copy_spec(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    spec = dict(body)
    options = spec.get("model_options")
    if isinstance(options, dict):
        spec["model_options"] = dict(options)
    return spec


def _model_options(spec: dict[str, Any]) -> dict[str, Any]:
    options = spec.get("model_options")
    return dict(options) if isinstance(options, dict) else {}


def _adapter_admission_block(adapter: Any) -> Optional[str]:
    """busy or draining from the adapter, before a pending tier is committed."""
    limited = adapter._concurrency_limited_response() if hasattr(adapter, "_concurrency_limited_response") else None
    if limited is not None:
        return "busy"
    draining = adapter._draining_response() if hasattr(adapter, "_draining_response") else None
    if draining is not None:
        return "draining"
    return None


class ContinuationService:
    def __init__(self, adapter: Any, *, journal_path: Path, admit=None, now=None):
        self.adapter = adapter
        self.journal = ContinuationJournal(journal_path)
        self.admit = admit or admit_continuation
        self._now = now or time.time
        self.running = False
        self._holder = f"clawd:{os.getpid()}:{id(self)}"
        self._tasks: set[asyncio.Task] = set()
        self._timers: set[asyncio.TimerHandle] = set()

    def open(self) -> None:
        self.journal.open()

    def start(self) -> None:
        self.running = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._spawn(self.recover())

    def stop(self) -> None:
        self.running = False
        self._cancel_timers()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self.journal.close()

    async def drain(self) -> None:
        """Cancel scheduler work and wait. Used by adapter shutdown so stop leaves no task."""
        self.running = False
        self._cancel_timers()
        tasks = [task for task in list(self._tasks) if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self.journal.close()

    def _cancel_timers(self) -> None:
        for handle in list(self._timers):
            handle.cancel()
        self._timers.clear()

    def status(self, session_id: str) -> dict[str, Any]:
        try:
            return self.journal.status(session_id)
        except JournalUnavailable as exc:
            return {"session_id": session_id, "error": str(exc), "request": None, "last_event": None}

    def fail_closed(self, session_id: str, reason: str) -> None:
        logger.error("clawd continuation fail-closed session=%s reason=%s", session_id, reason)
        try:
            if self.journal._conn is None:
                self.journal.open()
            self.journal.event(session_id, "blocked", reason, self._now())
        except JournalUnavailable:
            logger.exception("clawd continuation could not record %s", reason)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def recover(self) -> None:
        db = _db(self.adapter)
        if db is None:
            self.fail_closed("*", "ownership_unconfirmed")
            return
        now = self._now()
        try:
            pending = list(dict.fromkeys([
                *self.journal.pending_sessions(now),
                *self.journal.watched(now),
                *_recent_notice_sessions(db, now),
            ]))
        except JournalUnavailable as exc:
            self.fail_closed("*", "ownership_unconfirmed")
            logger.error("clawd continuation recovery skipped: %s", exc)
            return
        for session_id in pending:
            await self.evaluate(session_id, db, profile=_profile(self.adapter))

    async def evaluate(self, session_id: str, db: Any, *, profile: str) -> Decision:
        if not self.running:
            return Decision("skip", "stopped")
        try:
            decision = self._decide(session_id, db, profile)
        except JournalUnavailable:
            self.fail_closed(session_id, "ownership_unconfirmed")
            return Decision("block", "ownership_unconfirmed")
        except Exception:
            logger.exception("clawd continuation evaluation failed for %s", session_id)
            self.fail_closed(session_id, "ownership_unconfirmed")
            return Decision("block", "ownership_unconfirmed")
        await self._apply(session_id, db, profile, decision)
        return decision

    def _decide(self, session_id: str, db: Any, profile: str) -> Decision:
        rows = tuple(_load_rows(db, session_id))
        session = db.get_session(session_id) if hasattr(db, "get_session") else None
        route = None
        if session and hasattr(db, "get_recent_session_model_route"):
            route = db.get_recent_session_model_route(session_id)
        now = self._now()
        released = self.journal.reconcile_legacy_streak_receipts(session_id)
        if released:
            logger.warning(
                "clawd continuation released legacy streak receipts session=%s count=%s",
                session_id,
                released,
            )
        identities = [item for item in _identity_list(session_id, rows)]
        stored = self.journal.load_request(session_id)
        attempts = int(stored["attempts"]) if stored else 0
        snapshot = Snapshot(
            rows=rows, session=session, recent_route=route,
            lease_expires_at=turn_lease_expires(db, session_id, now),
            latest_run_status=_status(self.adapter, session_id, db),
            gates=collect_gates(self.adapter, session_id),
            now=now, receipted=self.journal.receipted(identities), attempts=attempts,
            foreign_holder=_foreign(self.adapter, session_id), store_ok=True, profile=profile,
        )
        if snapshot.latest_run_status is None and not _status_known(self.adapter, session_id, db):
            return Decision("block", "ownership_unconfirmed")
        return decide(snapshot, session_id=session_id)

    def _observe_admission_refusal(self, session_id: str, reason: str, now: float) -> None:
        """Record a refusal. Do not schedule, receipt, commit, or guess a profile.

        A missing authority will not appear by waiting in this process. Scheduling
        that refusal would self-excite. Burning MAX_ATTEMPTS would consume the
        notice, so this is not the failure budget either.
        """
        logger.error("clawd continuation admit refused session=%s reason=%s", session_id, reason)
        try:
            self.journal.event(session_id, "blocked", reason, now)
        except JournalUnavailable:
            self.fail_closed(session_id, reason)

    async def _budget_unlaunched(
        self, session_id: str, db: Any, profile: str, spec: dict[str, Any],
        identities: list[str], reason: str, now: float,
    ) -> None:
        """Spend one existing failure-budget slot without committing a pending tier."""
        frozen = self.journal.freeze(session_id, spec["request_key"], identities, spec, now)
        if not self.journal.claim(session_id, self._holder, now, _LEASE_S):
            return
        again = self._decide(session_id, db, profile)
        if again.action != "admit":
            self.journal.release(session_id, self._holder)
            await self._apply(session_id, db, profile, again)
            return
        attempt = int(frozen["attempts"]) + 1
        self.journal.record_attempt(session_id, attempt, reason, now + BACKOFF_S[0], now, "pending")
        self.journal.release(session_id, self._holder)
        self._schedule(session_id, db, profile, BACKOFF_S[0])

    async def _spec_for_freeze(
        self, session_id: str, db: Any, original: dict[str, Any], identities: list[str],
    ) -> dict[str, Any]:
        """Commit and re-read only when freeze will store a new body.

        An in-flight alias is returned as stored. Committing first would consume
        a pin that ``freeze`` will not write, and a new tier must not change the
        fingerprint of a run already reserved under that body.
        """
        inflight = self.journal.compatible_inflight(session_id, identities)
        if inflight is not None:
            return _copy_spec(inflight["body"])
        if _adapter_admission_block(self.adapter) is not None:
            return _copy_spec(original)
        return _copy_spec(await refresh_continuation_spec(self.adapter, original, db))

    async def _align_unreserved_body(
        self, session_id: str, db: Any, profile: str, spec: dict[str, Any], now: float,
    ) -> dict[str, Any]:
        """Commit into an unreserved in-flight body so the journal matches launch.

        A ``same`` or ``conflict`` reservation, or an unreadable store, blocks
        the rewrite. busy/draining also blocks it: those refusals stay before
        the commit.
        """
        if _adapter_admission_block(self.adapter) is not None:
            return spec
        if continuation_reservation(self.adapter, profile, spec) != "missing":
            return spec
        refreshed = _copy_spec(await refresh_continuation_spec(self.adapter, spec, db))
        if (
            _model_options(refreshed) == _model_options(spec)
            and refreshed.get("request_key") == spec.get("request_key")
        ):
            return spec
        if continuation_reservation(self.adapter, profile, spec) != "missing":
            return spec
        key = spec.get("request_key")
        if not isinstance(key, str) or not key:
            return spec
        if not self.journal.replace_inflight_body(session_id, key, refreshed, now):
            return spec
        return refreshed

    async def _apply(self, session_id: str, db: Any, profile: str, decision: Decision) -> None:
        now = self._now()
        if decision.action == "skip":
            return
        if decision.action == "block":
            self.journal.event(session_id, "blocked", decision.reason, now)
            logger.warning("clawd continuation blocked session=%s reason=%s", session_id, decision.reason)
            # streak_limit must not be receipted. NULL run_id alone is not a reason.
            if decision.reason in _RECEIPT_ON_BLOCK and decision.identities:
                self.journal.add_receipts(
                    list(decision.identities), session_id, None, now, reason=decision.reason,
                )
            return
        if decision.action == "defer":
            self.journal.watch(session_id, now + 2 * 3600)
            self.journal.event(session_id, "deferred", decision.reason, now)
            self._schedule(session_id, db, profile, decision.delay or BACKOFF_S[0])
            return
        original = _copy_spec(decision.spec or {})
        identities = list(decision.identities)
        try:
            prepared = await self._spec_for_freeze(session_id, db, original, identities)
        except ContinuationAdmitError as exc:
            if exc.reason in {"busy", "draining"}:
                await self._budget_unlaunched(
                    session_id, db, profile, original, identities, exc.reason, now,
                )
                return
            self._observe_admission_refusal(session_id, exc.reason, now)
            return
        freeze_key = prepared.get("request_key") or original["request_key"]
        frozen = self.journal.freeze(session_id, freeze_key, identities, prepared, now)
        spec = _copy_spec(frozen["body"])
        if not self.journal.claim(session_id, self._holder, now, _LEASE_S):
            return
        again = self._decide(session_id, db, profile)
        if again.action != "admit":
            self.journal.release(session_id, self._holder)
            await self._apply(session_id, db, profile, again)
            return
        try:
            spec = await self._align_unreserved_body(session_id, db, profile, spec, now)
        except ContinuationAdmitError as exc:
            if exc.reason in {"busy", "draining"}:
                attempt = int(frozen["attempts"]) + 1
                self.journal.record_attempt(
                    session_id, attempt, exc.reason, now + BACKOFF_S[0], now, "pending",
                )
                self.journal.release(session_id, self._holder)
                self._schedule(session_id, db, profile, BACKOFF_S[0])
                return
            self.journal.release(session_id, self._holder)
            self._observe_admission_refusal(session_id, exc.reason, now)
            return
        attempt = int(frozen["attempts"]) + 1
        self.journal.record_attempt(session_id, attempt, "", now, now, "leased")
        token = FROZEN_LAUNCH.set(True)
        try:
            try:
                run_id = await self.admit(self.adapter, spec, db=db, profile=profile)
            except (ContinuationAdmitError, JournalUnavailable) as exc:
                reason = getattr(exc, "reason", None) or type(exc).__name__
                self.journal.release(session_id, self._holder)
                if reason in {"busy", "draining"}:
                    self.journal.record_attempt(session_id, attempt, reason, now + BACKOFF_S[0], now, "pending")
                    self._schedule(session_id, db, profile, BACKOFF_S[0])
                    return
                self.journal.event(session_id, "blocked", reason, now)
                logger.error("clawd continuation admit refused session=%s reason=%s", session_id, reason)
                return
            self.journal.add_receipts(
                list(frozen["aliases"]), session_id, run_id, now, reason=_ADMITTED_RECEIPT_REASON,
            )
            self.journal.event(session_id, "admitted", run_id, now)
        finally:
            FROZEN_LAUNCH.reset(token)

    def _schedule(self, session_id: str, db: Any, profile: str, delay: float) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def _fire() -> None:
            self._timers.discard(handle)
            if self.running:
                self._spawn(self.evaluate(session_id, db, profile=profile))

        handle = loop.call_later(delay, _fire)
        self._timers.add(handle)


def _load_rows(db: Any, session_id: str) -> list[dict[str, Any]]:
    rows = db.get_messages(session_id, limit=300, latest=True)
    return [row for row in rows if isinstance(row, dict)]


def _identity_list(session_id: str, rows: tuple | list) -> list[str]:
    from gateway.clawd_continuation_decision import continuation_identity, is_real_completion
    found = []
    for row in rows:
        if is_real_completion(row):
            identity = continuation_identity(session_id, row)
            if identity:
                found.append(identity)
    return found


def _status(adapter: Any, session_id: str, db: Any) -> Optional[str]:
    status, known = latest_run_status(adapter, session_id, db)
    return status if known else None


def _status_known(adapter: Any, session_id: str, db: Any) -> bool:
    _status_value, known = latest_run_status(adapter, session_id, db)
    return known


def _foreign(adapter: Any, session_id: str) -> Optional[bool]:
    probe = getattr(adapter, "clawd_foreign_holder", None)
    if not callable(probe):
        return False
    try:
        value = probe(session_id)
    except Exception:
        return None
    if value is None:
        return None
    return bool(value)


def _recent_notice_sessions(db: Any, now: float) -> list[str]:
    try:
        rows = db._read_all(
            "SELECT DISTINCT session_id FROM messages WHERE display_kind = ? AND active = 1 AND timestamp > ?",
            ("async_delegation_complete", now - 2 * 3600),
        )
    except Exception:
        logger.exception("clawd continuation notice scan failed")
        return []
    found = []
    for row in rows or []:
        sid = row["session_id"] if isinstance(row, dict) else row[0]
        if isinstance(sid, str) and sid:
            found.append(sid)
    return found


def continuation_status_response(adapter: Any, request: Any):
    """GET handler body. Reads the journal and does not admit."""
    auth = adapter._check_auth(request) if hasattr(adapter, "_check_auth") else None
    if auth is not None:
        return auth
    session_id = request.match_info["session_id"]
    from aiohttp import web
    return web.json_response(read_status(adapter, session_id))
