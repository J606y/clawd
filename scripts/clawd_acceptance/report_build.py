"""Machine report assembly. A prepare exit of 0 is not acceptance green.
"""

from __future__ import annotations

from constants import PRODUCER, REQUESTED_MODEL
from classify import dedupe_checks, summary_counts
from fingerprints import artifact_report, source_identity, utc_now


def default_identity() -> dict:
    return {
        "requested_model": REQUESTED_MODEL,
        "expected": REQUESTED_MODEL,
        "observed": None,
        "observed_runtime_model": None,
        "mismatch": None,
        "can_pass": False,
        "parent_agent_checked": False,
        "note": "指定验收模型只写入外部用例请求。报告不假设 runtime.model，也不用它核对当前父代理。",
    }


def assemble(
    *,
    executed: bool,
    phase: str,
    gate: dict,
    isolation: dict,
    toolchain: dict,
    stages: list[dict],
    checks: list[dict],
    identity: dict | None = None,
    repo=None,
    wheel_dir=None,
) -> dict:
    for item in checks:
        if item.get("id") == "deploy-support" and item.get("status") == "pass":
            item["status"] = "blocked"
            item["detail"] = "未应用模板不能记成部署支持已验证"
    summary = summary_counts(checks)
    unique, _excluded = dedupe_checks(checks)
    summary["blocked_ids"] = [item["id"] for item in unique if item.get("status") == "blocked" and item.get("required", True)]
    summary["failed_ids"] = [item["id"] for item in unique if item.get("status") == "fail" and item.get("required", True)]
    captured = utc_now()
    source = source_identity(repo, captured) if repo is not None else {"complete": False, "fingerprint": None, "captured_at": captured}
    artifacts = artifact_report(repo, wheel_dir=wheel_dir) if repo is not None else {"complete": False, "items": [], "blockers": ["repo not recorded"]}
    blockers = _blockers(checks, source, artifacts)
    plan_only = phase == "prepare" or not executed
    green = _green(phase, executed, checks, source, artifacts, blockers)
    if any(item.get("status") == "fail" and item.get("required", True) for item in checks):
        summary["exit_code"] = 1
        green = False
    elif not green:
        summary["exit_code"] = 2
    else:
        summary["exit_code"] = 0
    summary["status"] = {0: "pass", 1: "fail", 2: "blocked"}.get(summary["exit_code"], "blocked")
    return {
        "schema_version": 1,
        "kind": "clawd-final-acceptance",
        "producer": PRODUCER,
        "executed": executed,
        "never_executed": not executed,
        "phase": phase,
        "plan_only": plan_only,
        "acceptance_green": green,
        "final_acceptance": green,
        "covers_cutover": green and _check_passed(checks, "rollback"),
        "generated_at": captured,
        "identity": identity or default_identity(),
        "source_identity": source,
        "artifacts": artifacts,
        "blockers": blockers,
        "gate": gate,
        "isolation": isolation,
        "toolchain": toolchain,
        "stages": stages,
        "checks": checks,
        "summary": summary,
    }


def _check_passed(checks: list[dict], cid: str) -> bool:
    rows = [item for item in checks if item.get("id") == cid]
    return bool(rows) and all(item.get("status") == "pass" for item in rows)


def _blockers(checks: list[dict], source: dict, artifacts: dict) -> list[str]:
    found = [f"check:{item.get('id')}:{item.get('status')}" for item in checks if item.get("required", True) and item.get("status") != "pass"]
    if not source.get("complete"):
        found.append("source-identity-incomplete")
    found.extend(f"artifact:{item}" for item in artifacts.get("blockers") or [])
    return found


def _green(phase: str, executed: bool, checks: list[dict], source: dict, artifacts: dict, blockers: list[str]) -> bool:
    if phase != "run" or not executed or blockers or not source.get("complete") or not artifacts.get("complete"):
        return False
    required = [item for item in checks if item.get("required", True)]
    return bool(required) and all(item.get("status") == "pass" for item in required)
