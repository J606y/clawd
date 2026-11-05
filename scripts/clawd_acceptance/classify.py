"""Status aggregation, identity, timeout and wheel classification."""

from __future__ import annotations

import json

from constants import (
    EXPECTED_IDENTITY,
    FORBIDDEN_KILL_SECONDS,
    NODE_FULL_KNOWN_SECONDS,
    NODE_FULL_TIMEOUT_SECONDS,
    NOT_IDENTITY_MARKERS,
    WHEEL_MEMBERS,
)

STATUS_RANK = {
    "fail": 6,
    "blocked": 5,
    "unknown": 4,
    "interrupted": 4,
    "in_progress": 4,
    "not_run": 3,
    "pass": 1,
    "ready": 2,
}


def timeout_rejected(seconds: int, *, node_wall: bool = False) -> str | None:
    if seconds in FORBIDDEN_KILL_SECONDS:
        return f"kill timeout {seconds}s is forbidden; it is not evidence the product is broken"
    if node_wall and seconds <= NODE_FULL_KNOWN_SECONDS:
        return (
            f"node wall timeout {seconds}s is at or below the known full-suite duration "
            f"{NODE_FULL_KNOWN_SECONDS}s"
        )
    if node_wall and seconds < NODE_FULL_TIMEOUT_SECONDS:
        return f"node wall timeout {seconds}s is below the orchestrator floor {NODE_FULL_TIMEOUT_SECONDS}s"
    return None


def classify_timeout(*, configured: int, killed: bool, assertion_failure: bool, node_wall: bool) -> str:
    if timeout_rejected(configured, node_wall=node_wall) and killed:
        return "blocked"
    if not killed:
        return "fail" if assertion_failure else "pass"
    if assertion_failure:
        return "fail"
    return "blocked"


def classify_identity(runtime_model: str | None, markers: list[str] | None = None) -> dict:
    found = [marker for marker in (markers or []) if marker in NOT_IDENTITY_MARKERS or marker == "test-not-llm"]
    base = {
        "requested_model": EXPECTED_IDENTITY,
        "expected": EXPECTED_IDENTITY,
        "observed_runtime_model": runtime_model,
        "observed": runtime_model,
        "parent_agent_checked": False,
        "markers": found,
    }
    if found or (runtime_model in NOT_IDENTITY_MARKERS):
        return {
            **base,
            "mismatch": True,
            "can_pass": False,
            "markers": found or ([runtime_model] if runtime_model in NOT_IDENTITY_MARKERS else []),
        }
    if not runtime_model:
        return {**base, "mismatch": None, "can_pass": False, "markers": []}
    if runtime_model == EXPECTED_IDENTITY:
        return {**base, "mismatch": False, "can_pass": True, "markers": []}
    return {**base, "mismatch": True, "can_pass": False, "markers": []}


def classify_run_payload(payload: dict) -> dict:
    """Record a served runtime model only when the payload actually has one.

    The requested acceptance model is not proof that runtime.model exists.
    A missing field is unknown, not a mismatch against the parent agent.
    """
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    served = runtime.get("model") if isinstance(runtime, dict) else None
    blob = json.dumps(payload, ensure_ascii=False)
    markers = [marker for marker in NOT_IDENTITY_MARKERS if marker in blob]
    identity = classify_identity(served if isinstance(served, str) else None, markers)
    if markers or served in NOT_IDENTITY_MARKERS:
        return {"status": "fail", "identity": identity, "detail": "fake executor or advertised runtime cannot pass"}
    if not served:
        return {
            "status": "blocked",
            "identity": identity,
            "detail": "runtime field absent; unknown served model is not pass and is not a parent-agent mismatch",
        }
    if payload.get("status") != "completed":
        return {"status": "blocked", "identity": identity, "detail": "turn not completed"}
    if served != EXPECTED_IDENTITY:
        return {
            "status": "blocked",
            "identity": identity,
            "detail": "served runtime differs from the requested model; not used to fail the parent agent",
        }
    return {"status": "pass", "identity": identity, "detail": "payload included the requested served model"}


def classify_external_case(payload: dict | None, *, requested_model: str) -> dict:
    """Judge the external case by the request we send, not by an assumed runtime field."""
    if not payload:
        return {"status": "blocked", "detail": "no external turn", "requested_model": requested_model}
    blob = json.dumps(payload, ensure_ascii=False)
    if any(marker in blob for marker in NOT_IDENTITY_MARKERS):
        return {"status": "fail", "detail": "test-not-llm or hermes-agent marker cannot pass", "requested_model": requested_model}
    if payload.get("status") != "completed":
        return {"status": "blocked", "detail": "external turn not completed", "requested_model": requested_model}
    if payload.get("_requested_model") != requested_model:
        return {"status": "blocked", "detail": "request model stamp absent; not assuming runtime.model", "requested_model": requested_model}
    return {
        "status": "pass",
        "detail": f"completed external turn requested {requested_model}; runtime.model was not required",
        "requested_model": requested_model,
    }


def classify_mcp(status: int, body: bytes) -> str:
    compact = b"".join(body.split())
    if status != 200 or compact in {b'{"data":[]}', b""}:
        return "blocked"
    return "pass"


def inspect_wheel_names(names: list[str]) -> dict:
    missing = [member for member in WHEEL_MEMBERS if member not in names]
    desktop = [name for name in names if name.startswith("apps/desktop/")]
    panel = [name for name in names if name.startswith("apps/panel/")]
    members_ok = not missing and not desktop and not panel
    return {
        "members_status": "pass" if members_ok else "fail",
        "missing": missing,
        "desktop": desktop[:8],
        "panel_in_wheel": bool(panel),
        "installed_panel_status": "blocked",
        "installed_panel_detail": "wheel members do not prove panel assets or sidecar run outside the source tree",
    }


def _rank(check: dict) -> int:
    status = check.get("status")
    if not isinstance(status, str):
        return 0
    return STATUS_RANK.get(status, 0)


def dedupe_checks(checks: list[dict]) -> tuple[list[dict], int]:
    chosen: dict[str, dict] = {}
    for check in checks:
        cid = str(check.get("id") or "")
        if not cid:
            continue
        prev = chosen.get(cid)
        if prev is None or _rank(check) > _rank(prev):
            chosen[cid] = check
    raw = len([check for check in checks if check.get("id")])
    unique = list(chosen.values())
    return unique, raw - len(unique)


def aggregate_exit(checks: list[dict]) -> int:
    required = [check for check in checks if check.get("required", True)]
    if not required:
        return 2
    statuses = {check.get("status") for check in required}
    if "fail" in statuses:
        return 1
    if statuses == {"pass"}:
        return 0
    return 2


def summary_counts(checks: list[dict]) -> dict:
    unique, excluded = dedupe_checks(checks)
    required = [check for check in unique if check.get("required", True)]
    counts = {status: 0 for status in ("pass", "fail", "blocked", "not_run", "unknown", "interrupted", "in_progress")}
    for check in required:
        status = str(check.get("status"))
        if status in counts:
            counts[status] += 1
        else:
            counts["unknown"] += 1
    return {
        "exit_code": aggregate_exit(required),
        "status": _exit_name(aggregate_exit(required)),
        "unique": counts,
        "raw_rows": len(checks),
        "unique_rows": len(unique),
        "overlap_excluded": excluded,
        "annotations_not_summed": True,
        "note": "unique required check ids only; stage passed-counts are not added together",
    }


def _exit_name(code: int) -> str:
    return {0: "pass", 1: "fail", 2: "blocked"}.get(code, "blocked")


def inclusion_from_log(log_text: str, rel_dir: str, suite_exit: int | None) -> str:
    if suite_exit is None:
        return "not_run"
    if rel_dir not in log_text:
        return "blocked" if not log_text.strip() else "fail"
    if f"FAILED {rel_dir}" in log_text or f"ERROR {rel_dir}" in log_text:
        return "fail"
    if suite_exit == 0:
        return "pass"
    if suite_exit == 124:
        return "blocked"
    return "fail"
