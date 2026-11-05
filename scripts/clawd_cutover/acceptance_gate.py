"""Consume a final acceptance report. A short pass object is not enough.

This module reads JSON and re-hashes repo-relative artifacts. It does not
start acceptance, migration, or a service.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from versions import _git_head, _pyproject_version


REPO_ROOT = Path(__file__).resolve().parents[2]
KIND = "clawd-final-acceptance"
PRODUCER = "scripts/clawd_acceptance"
SCHEMA_VERSION = 1
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_NOT_PASS = frozenset({"not_run", "unknown", "blocked", "interrupted", "in_progress", "ready", "fail"})
_BANNED_PREFIXES = (
    "/root/.hermes",
    "/usr/local/lib/hermes-agent",
    "/usr/local/bin/hermes",
)


def evaluate_report(path: Path | None, *, mode: str) -> dict:
    block = {
        "required_for_apply": True,
        "status": "missing",
        "blocks_apply": mode == "apply",
        "producer_seen": None,
        "final_acceptance": False,
        "covers_cutover": False,
        "plan_only": None,
        "generated_at": None,
        "source_fingerprint": None,
        "artifact_match": None,
        "reasons": [] if path is not None else ["missing"],
        "note": "只接受 scripts/clawd_acceptance 的完整运行报告；准备退出 0 不是验收全绿。",
    }
    if path is None:
        return block
    loaded, error = _load(path)
    if error or loaded is None:
        block["status"] = "malformed"
        block["blocks_apply"] = True
        block["reasons"] = [error or "unreadable"]
        return block
    reasons = rejection_reasons(loaded, repo=REPO_ROOT)
    producer = loaded.get("producer")
    generated = loaded.get("generated_at")
    identity = loaded.get("source_identity") if isinstance(loaded.get("source_identity"), dict) else {}
    block["producer_seen"] = producer if isinstance(producer, str) else None
    block["generated_at"] = generated if isinstance(generated, str) else None
    block["source_fingerprint"] = identity.get("fingerprint")
    block["plan_only"] = loaded.get("plan_only") is True or loaded.get("phase") == "prepare"
    block["final_acceptance"] = not reasons and loaded.get("final_acceptance") is True
    block["covers_cutover"] = not reasons and _rollback_passed(loaded)
    block["artifact_match"] = None if reasons else True
    block["reasons"] = reasons
    block["status"] = "passed" if not reasons else "rejected"
    block["blocks_apply"] = bool(reasons) if mode == "apply" else False
    if mode != "apply":
        block["note"] = "非 apply 不把验收文件当成切换授权。"
    return block


def rejection_reasons(raw: object, *, repo: Path) -> list[str]:
    if not isinstance(raw, dict):
        return ["not-object"]
    if "acceptance_exit" in raw and "kind" not in raw:
        return ["prepare-plan-wrapper"]
    reasons: list[str] = []
    if raw.get("schema_version") != SCHEMA_VERSION:
        reasons.append("schema_version")
    if raw.get("kind") != KIND:
        reasons.append("kind")
    if raw.get("producer") != PRODUCER:
        reasons.append("producer")
    if raw.get("phase") != "run":
        reasons.append("phase")
    if raw.get("executed") is not True or raw.get("never_executed") is not False:
        reasons.append("not-executed")
    if raw.get("plan_only") is not False:
        reasons.append("plan-only")
    if raw.get("acceptance_green") is not True or raw.get("final_acceptance") is not True:
        reasons.append("not-green")
    if not _TIME.fullmatch(str(raw.get("generated_at") or "")):
        reasons.append("generated_at")
    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    if summary.get("exit_code") != 0 or summary.get("status") != "pass":
        reasons.append("summary")
    reasons.extend(_check_reasons(raw.get("checks")))
    if raw.get("blockers") not in ([],):
        reasons.append("blockers")
    if not _rollback_passed(raw):
        reasons.append("covers_cutover")
    if _deploy_claimed(raw):
        reasons.append("deploy-claimed")
    reasons.extend(_identity_reasons(raw.get("source_identity"), repo))
    reasons.extend(_artifact_reasons(raw.get("artifacts"), repo))
    if _identity_used_as_parent_gate(raw):
        reasons.append("parent-runtime-model-gate")
    return reasons


def _load(path: Path) -> tuple[dict | None, str | None]:
    if path.is_symlink() or not path.is_file():
        return None, "not-a-file"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "unreadable"
    if not isinstance(raw, dict):
        return None, "not-object"
    return raw, None


def _check_reasons(checks: object) -> list[str]:
    if not isinstance(checks, list) or not checks:
        return ["checks-empty"]
    reasons: list[str] = []
    required = 0
    for item in checks:
        if not isinstance(item, dict) or not item.get("id"):
            reasons.append("check-shape")
            continue
        if item.get("required") is False:
            continue
        required += 1
        status = item.get("status")
        if status != "pass" or status in _NOT_PASS:
            reasons.append(f"check:{item.get('id')}:{status}")
    if required == 0:
        reasons.append("no-required-checks")
    return reasons


def _rollback_passed(raw: dict) -> bool:
    checks = raw.get("checks")
    if not isinstance(checks, list):
        return False
    found = [item for item in checks if isinstance(item, dict) and item.get("id") == "rollback"]
    return bool(found) and all(item.get("status") == "pass" and item.get("required") is not False for item in found)


def _deploy_claimed(raw: dict) -> bool:
    checks = raw.get("checks")
    if not isinstance(checks, list):
        return False
    for item in checks:
        if isinstance(item, dict) and item.get("id") == "deploy-support" and item.get("status") == "pass":
            return True
    return False


def _identity_reasons(identity: object, repo: Path) -> list[str]:
    if not isinstance(identity, dict):
        return ["source-identity"]
    if not _TIME.fullmatch(str(identity.get("captured_at") or "")):
        return ["source-captured-at"]
    head = _git_head(repo)
    version = _pyproject_version(repo)
    if not head or not version:
        return ["live-source-identity-unknown"]
    if identity.get("git_head") != head or identity.get("pyproject_version") != version:
        return ["source-identity-mismatch"]
    expected = _fingerprint(head, version)
    if identity.get("fingerprint") != expected:
        return ["source-fingerprint"]
    return []


def _artifact_reasons(artifacts: object, repo: Path) -> list[str]:
    if not isinstance(artifacts, dict) or artifacts.get("complete") is not True:
        return ["artifacts-incomplete"]
    items = artifacts.get("items")
    if not isinstance(items, list) or not items:
        return ["artifacts-empty"]
    reasons: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            reasons.append("artifact-shape")
            continue
        rel = item.get("path")
        digest = item.get("sha256")
        if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts:
            reasons.append("artifact-path")
            continue
        if any(rel.startswith(prefix) for prefix in _BANNED_PREFIXES):
            reasons.append("artifact-banned")
            continue
        path = repo / rel
        if path.is_symlink() or not path.is_file():
            reasons.append(f"artifact-missing:{rel}")
            continue
        if not isinstance(digest, str) or _sha256(path) != digest:
            reasons.append(f"artifact-mismatch:{rel}")
    return reasons


def _identity_used_as_parent_gate(raw: dict) -> bool:
    identity = raw.get("identity")
    if not isinstance(identity, dict):
        return False
    if identity.get("parent_agent_checked") is True:
        return True
    note = str(identity.get("note") or "")
    return "runtime.model==grok-4.7" in note


def _fingerprint(head: str, version: str) -> str:
    payload = f"{version}\n{head}\n".encode()
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
