"""Pure continuation decisions. No I/O, no run admission, no model prompt.

A completion notice starts a follow-up only when it is a real (not interim or
suppressed) delivery and it sits after the last assistant reply and the last
human input. Gates are data on the snapshot; this module never asks the model
to honor them. A run of successful continuations is not a gate.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

WINDOW_S = 2 * 3600
# Observational only. decide() must not block when a success streak reaches this.
MAX_STREAK = 5
MAX_ATTEMPTS = 5
BACKOFF_S = (2.0, 5.0, 10.0, 20.0, 40.0)
DISPLAY_KIND = "clawd_continuation"
MARK = "[clawd自动续跑]"
LEGACY_MARK = "[面板自动续跑]"
# Fixed instruction. No gate waiver, no task source, no original user prompt.
CONTINUATION_TEXT = (
    f"{MARK} 你之前派出的后台子任务已完成，结果见上方的完成通知。"
    "请据此继续完成之前的任务。"
)
_SCAFFOLD_PREFIXES = (
    "[System:", "[CONTEXT", "[PRIOR CONTEXT", "[IMPORTANT: Background",
    "[Your active task list", "[Planning state preserved", "[Skills pruned",
    "[ASYNC DELEGATION", "Cronjob Response:",
)
_INTERIM_PREFIX = "[ASYNC DELEGATION TASK FAILED — "
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,160}")
_NOTICE_RE = re.compile(
    r"^\[ASYNC DELEGATION (?:BATCH )?COMPLETE — ([A-Za-z0-9_-]{1,160})\](?:\n|$)"
)
_PLAN_ACTIONS = frozenset({"submit", "approve", "request_approval"})


@dataclass(frozen=True)
class Gates:
    approval: bool = False
    clarify: bool = False
    plan: bool = False
    unknown: bool = False


@dataclass(frozen=True)
class Snapshot:
    rows: tuple[Mapping[str, Any], ...]
    session: Optional[Mapping[str, Any]]
    recent_route: Optional[Mapping[str, Any]]
    lease_expires_at: Optional[float]
    latest_run_status: Optional[str]
    gates: Gates = field(default_factory=Gates)
    now: float = 0.0
    receipted: frozenset[str] = frozenset()
    attempts: int = 0
    foreign_holder: Optional[bool] = False
    store_ok: bool = True
    profile: str = "default"


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    spec: Optional[dict[str, Any]] = None
    identities: tuple[str, ...] = ()
    delay: float = 0.0


def row_text(row: Mapping[str, Any]) -> str:
    content = row.get("content")
    if not isinstance(content, str):
        return ""
    if content.startswith("\x00json:"):
        try:
            parts = json.loads(content[len("\x00json:"):])
        except ValueError:
            return ""
        if not isinstance(parts, list):
            return ""
        return "\n\n".join(
            part["text"] for part in parts
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
        )
    return content


def metadata_of(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("display_metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def is_human(row: Mapping[str, Any]) -> bool:
    """A person wrote this row. Notices, scaffolds and continuation marks are not."""
    if row.get("role") != "user":
        return False
    kind = row.get("display_kind") or ""
    if kind == "steer":
        return True
    if kind:
        return False
    head = row_text(row).lstrip()
    if head.startswith((MARK, LEGACY_MARK)):
        return False
    return not head.startswith(_SCAFFOLD_PREFIXES)


def is_continuation_row(row: Mapping[str, Any]) -> bool:
    if row.get("role") != "user":
        return False
    if row.get("display_kind") == DISPLAY_KIND:
        return True
    if row.get("display_kind"):
        return False
    return row_text(row).lstrip().startswith((MARK, LEGACY_MARK))


def _interim(row: Mapping[str, Any], text: str) -> bool:
    meta = metadata_of(row)
    return bool(meta.get("delivery_notice")) or text.startswith(_INTERIM_PREFIX)


def is_real_completion(row: Mapping[str, Any]) -> bool:
    if row.get("display_kind") != "async_delegation_complete":
        return False
    text = row_text(row)
    meta = metadata_of(row)
    if meta.get("presentation_suppressed") is True or _interim(row, text):
        return False
    return True


def notice_delegation_id(row: Mapping[str, Any]) -> Optional[str]:
    if not is_real_completion(row):
        return None
    did = metadata_of(row).get("delegation_id")
    if isinstance(did, str) and _ID_RE.fullmatch(did):
        return did
    match = _NOTICE_RE.match(row_text(row))
    return match.group(1) if match else None


def continuation_identity(session_id: str, row: Mapping[str, Any]) -> Optional[str]:
    did = notice_delegation_id(row)
    if did:
        return f"clawd-continue-{session_id}-{did}"
    row_id = row.get("id")
    if isinstance(row_id, int):
        return f"clawd-continue-{session_id}-row-{row_id}"
    return None


def request_key(session_id: str, identities: Sequence[str]) -> str:
    ordered = tuple(sorted(set(identities)))
    if len(ordered) == 1:
        raw = ordered[0]
    else:
        digest = hashlib.sha256("\n".join(ordered).encode()).hexdigest()[:32]
        raw = f"clawd-continue-set-{session_id}-{digest}"
    if 1 <= len(raw) <= 255 and all(33 <= ord(ch) <= 126 for ch in raw):
        return raw
    return "clawd-continue-" + hashlib.sha256(raw.encode()).hexdigest()


def _index_of(rows: Sequence[Mapping[str, Any]], pred) -> int:
    found = -1
    for index, row in enumerate(rows):
        if pred(row):
            found = index
    return found


def open_notices(rows: Sequence[Mapping[str, Any]], *, now: float, session_id: str) -> list[tuple[int, Mapping[str, Any], str]]:
    """Real completions after the last assistant reply and the last human input."""
    last_assistant = _index_of(rows, lambda row: row.get("role") == "assistant")
    last_human = _index_of(rows, is_human)
    barrier = max(last_assistant, last_human)
    found = []
    for index, row in enumerate(rows):
        if index <= barrier or not is_real_completion(row):
            continue
        timestamp = row.get("timestamp")
        if isinstance(timestamp, (int, float)) and timestamp <= now - WINDOW_S:
            continue
        identity = continuation_identity(session_id, row)
        if identity:
            found.append((index, row, identity))
    return found


def streak_count(rows: Sequence[Mapping[str, Any]]) -> int:
    """Continuation rows after the last human input. Observational; not a gate."""
    last_human = _index_of(rows, is_human)
    return sum(1 for index, row in enumerate(rows) if index > last_human and is_continuation_row(row))


def transcript_plan_pending(rows: Sequence[Mapping[str, Any]]) -> bool:
    """An unanswered plan_mode submit/approve is a gate, not a model hint."""
    last_human = _index_of(rows, is_human)
    for row in rows[last_human + 1:]:
        if metadata_of(row).get("plan_approval") is True:
            return True
        if metadata_of(row).get("kind") in {"plan", "plan_approval"}:
            return True
        calls = row.get("tool_calls")
        if isinstance(calls, str):
            try:
                calls = json.loads(calls)
            except ValueError:
                calls = None
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else call
            name = fn.get("name") if isinstance(fn, dict) else None
            if name != "plan_mode":
                continue
            raw_args = fn.get("arguments") if isinstance(fn, dict) else None
            args = raw_args
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except ValueError:
                    args = {}
            action = args.get("action") if isinstance(args, dict) else None
            if action in _PLAN_ACTIONS:
                return True
    return False


def _parse_model_config(session: Mapping[str, Any]) -> dict[str, Any]:
    raw = session.get("model_config")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _effort_options(config: Mapping[str, Any]) -> dict[str, Any]:
    options = config.get("model_options")
    if isinstance(options, dict) and options:
        return dict(options)
    reasoning = config.get("reasoning")
    effort = None
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
    effort = config.get("reasoning_effort", effort)
    if isinstance(effort, str) and effort.strip():
        return {"reasoning_effort": effort.strip()}
    return {}


def inherit_runtime(session: Mapping[str, Any], recent_route: Optional[Mapping[str, Any]]) -> tuple[Optional[dict[str, Any]], str]:
    """Session lock, else the last coherent model+provider. Never a global default.

    Does not read ``clawd_pending_reasoning_effort``. A pin is not a stored
    tier. The continuation service commits it under the admission lock before
    ``journal.freeze``, then calls this function again. Reading the pin here
    would freeze an uncommitted tier and the next locked turn could drop it.
    """
    config = _parse_model_config(session)
    lock = config.get("browser_model_lock")
    if isinstance(lock, dict) and lock.get("confirmed"):
        model = lock.get("model") or session.get("model")
        provider = lock.get("provider")
        if not isinstance(model, str) or not model.strip() or not isinstance(provider, str) or not provider.strip():
            return None, "provider_unresolved"
        options = lock.get("model_options") if isinstance(lock.get("model_options"), dict) else _effort_options(config)
        return {
            "model": model.strip(), "provider": provider.strip(),
            "model_options": dict(options or {}), "locked": True,
        }, ""
    model = session.get("model")
    provider = (recent_route or {}).get("billing_provider") or (recent_route or {}).get("provider")
    if not isinstance(model, str) or not model.strip() or not isinstance(provider, str) or not provider.strip():
        return None, "provider_unresolved"
    if recent_route and recent_route.get("model") not in (None, "", model):
        return None, "provider_unresolved"
    return {
        "model": model.strip(), "provider": provider.strip(),
        "model_options": _effort_options(config), "locked": False,
    }, ""


def _backoff(attempts: int) -> float:
    index = min(max(attempts - 1, 0), len(BACKOFF_S) - 1)
    return BACKOFF_S[index]


def decide(snapshot: Snapshot, *, session_id: str) -> Decision:
    """Return skip, block, defer or admit. Admit carries a frozen, non-secret spec."""
    if not snapshot.store_ok or snapshot.foreign_holder is None:
        return Decision("block", "ownership_unconfirmed")
    session = snapshot.session
    if not session:
        return Decision("block", "ownership_unconfirmed")
    if session.get("source") != "api_server":
        return Decision("skip", "not_api_source")
    if session.get("ended_at"):
        return Decision("skip", "session_ended")
    profile = session.get("profile_name")
    if profile and snapshot.profile and profile != snapshot.profile:
        return Decision("block", "ownership_unconfirmed")
    if snapshot.foreign_holder:
        return Decision("block", "foreign_holder")
    if snapshot.gates.unknown:
        return Decision("block", "gate_unknown")
    identities_ready = open_notices(snapshot.rows, now=snapshot.now, session_id=session_id)
    pending = [item for item in identities_ready if item[2] not in snapshot.receipted]
    if not pending:
        return Decision("skip", "no_trigger")
    identities = tuple(item[2] for item in pending)
    if snapshot.latest_run_status == "cancelled":
        return Decision("block", "cancelled", identities=identities)
    if snapshot.gates.approval:
        return Decision("block", "approval_pending", identities=identities)
    if snapshot.gates.clarify:
        return Decision("block", "clarify_pending", identities=identities)
    if snapshot.gates.plan or transcript_plan_pending(snapshot.rows):
        return Decision("block", "plan_pending", identities=identities)
    # Success streak is observational via streak_count / MAX_STREAK. It is not a gate.
    if snapshot.attempts >= MAX_ATTEMPTS:
        return Decision("block", "attempts_exhausted", identities=identities)
    lease = snapshot.lease_expires_at
    if isinstance(lease, (int, float)) and lease > snapshot.now:
        return Decision("defer", "busy", identities=identities, delay=_backoff(snapshot.attempts + 1))
    runtime, reason = inherit_runtime(session, snapshot.recent_route)
    if runtime is None:
        return Decision("block", reason or "provider_unresolved", identities=identities)
    spec = {
        "input": CONTINUATION_TEXT,
        "session_id": session_id,
        "model": runtime["model"],
        "provider": runtime["provider"],
        "model_options": runtime["model_options"],
        "locked": runtime["locked"],
        "notice_ids": [notice_delegation_id(item[1]) or str(item[1].get("id")) for item in pending],
        "request_key": request_key(session_id, identities),
        "display_kind": DISPLAY_KIND,
        "cwd": session.get("cwd") if isinstance(session.get("cwd"), str) else "",
        "session_key": session.get("session_key") if isinstance(session.get("session_key"), str) else "",
    }
    return Decision("admit", "ready", spec=spec, identities=identities)
