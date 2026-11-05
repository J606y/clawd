"""Implementation-manifest gate and orchestrator-owned readiness checks.

Does not create or modify docs/clawd/implementation-completion-manifest.json.
implemented means the other lane marked code delivered, not that tests passed.
"""

from __future__ import annotations

import json
from pathlib import Path

from constants import (
    FORBIDDEN_PREFIXES,
    LICENSE_INVENTORY,
    MANIFEST_STATUSES,
    PRODUCTION_ROLLBACK,
    REQUIRED_CASE_SUITES,
)


def forbidden_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return True
    text = str(resolved)
    if resolved == PRODUCTION_ROLLBACK or text.endswith("/panel-deploy.sh"):
        return True
    for prefix in FORBIDDEN_PREFIXES:
        if text == prefix or text.startswith(prefix + "/"):
            return True
    return False


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        return {"ok": False, "reason": "missing", "items": [], "path": str(path)}
    if forbidden_path(path):
        return {"ok": False, "reason": "forbidden-path", "items": [], "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"unreadable: {exc}", "items": [], "path": str(path)}
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return {"ok": False, "reason": "schema_version", "items": [], "path": str(path)}
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return {"ok": False, "reason": "empty-items", "items": [], "path": str(path)}
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            return {"ok": False, "reason": "item-type", "items": [], "path": str(path)}
        for key in ("id", "scope", "owner", "status", "evidence", "blocking"):
            if key not in item:
                return {"ok": False, "reason": f"missing-{key}", "items": [], "path": str(path)}
        if item["status"] not in MANIFEST_STATUSES:
            return {"ok": False, "reason": f"bad-status:{item['status']}", "items": [], "path": str(path)}
        if not isinstance(item["evidence"], list) or not isinstance(item["blocking"], bool):
            return {"ok": False, "reason": "bad-evidence-or-blocking", "items": [], "path": str(path)}
        if not isinstance(item["id"], str) or not item["id"] or item["id"] in seen:
            return {"ok": False, "reason": "duplicate-or-empty-id", "items": [], "path": str(path)}
        seen.add(item["id"])
    return {"ok": True, "reason": "ok", "items": items, "path": str(path), "warnings": _warnings(items)}


def _warnings(items: list[dict]) -> list[str]:
    notes = []
    for item in items:
        if item.get("blocking") and item.get("status") == "implemented" and not item.get("evidence"):
            notes.append(f"{item['id']}: implemented with empty evidence; not a test pass")
    return notes


def blocking_not_implemented(items: list[dict]) -> list[str]:
    return [
        str(item.get("id"))
        for item in items
        if item.get("blocking") is True and item.get("status") != "implemented"
    ]


def manifest_allows_run(loaded: dict, implementation_complete: bool) -> dict:
    """Contract gate only. Does not mark any acceptance check passed."""
    pending = blocking_not_implemented(loaded.get("items") or [])
    allowed = bool(loaded.get("ok") and implementation_complete and not pending)
    return {
        "run_allowed": allowed,
        "implementation_complete_flag": bool(implementation_complete),
        "blocking_not_implemented": pending,
        "schema_ok": bool(loaded.get("ok")),
        "reason": loaded.get("reason"),
        "implemented_means": "code delivered, not tests passed",
        "tests_passed": False,
    }


def case_suite_status(repo: Path, spec: dict) -> dict:
    directory = repo / spec["rel_dir"]
    doc = repo / spec["rel_doc"]
    base = {"id": spec["id"], "rel_dir": spec["rel_dir"], "rel_doc": spec["rel_doc"]}
    if directory.exists() and forbidden_path(directory):
        return {**base, "status": "blocked", "detail": "case directory resolves to a forbidden path"}
    if not directory.is_dir():
        return {**base, "status": "blocked", "detail": f"{spec['rel_dir']} not delivered; acceptance will not create it"}
    files = [p for p in directory.rglob("*.py") if p.is_file() and p.name.startswith("test")]
    if not files:
        return {**base, "status": "blocked", "detail": "no test modules delivered; empty directory is not a pass"}
    if not doc.is_file():
        return {**base, "status": "blocked", "detail": f"{spec['rel_doc']} missing"}
    text = doc.read_text(encoding="utf-8", errors="replace")
    missing = [theme for theme in spec.get("themes") or () if theme not in text]
    if missing:
        return {**base, "status": "blocked", "detail": "preparation doc missing themes: " + ", ".join(missing)}
    if not spec.get("themes") and "接口" not in text and "interface" not in text.lower():
        return {**base, "status": "blocked", "detail": "preparation doc does not list interface dependencies"}
    return {
        **base,
        "status": "ready",
        "detail": "delivered; ready is not a test pass",
        "files": len(files),
        "interface_lines": _interface_lines(text),
    }


def _interface_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "接口" in line or "interface" in line.lower() or line.startswith(("- ", "* ")):
            lines.append(line[:240])
        if len(lines) >= 40:
            break
    return lines


def case_suites_ready(repo: Path) -> dict:
    checks = [case_suite_status(repo, spec) for spec in REQUIRED_CASE_SUITES]
    blocked = [item["id"] for item in checks if item["status"] != "ready"]
    return {"ok": not blocked, "checks": checks, "blocked": blocked}


def _inventory_text(root: Path) -> str:
    if root.is_file():
        return root.read_text(encoding="utf-8", errors="replace")[:12000]
    chunks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".py", ".md", ".json", ".txt"}:
            continue
        if forbidden_path(path):
            return ""
        chunks.append(path.read_text(encoding="utf-8", errors="replace")[:4000])
        if len(chunks) >= 30:
            break
    return "\n".join(chunks)


def inventory_ready(repo: Path | None = None) -> dict:
    root = (repo / "scripts" / "clawd_inventory") if repo is not None else LICENSE_INVENTORY
    if not root.exists():
        return {
            "ok": False,
            "status": "blocked",
            "network": False,
            "detail": "scripts/clawd_inventory missing; network license audit refused",
        }
    if forbidden_path(root):
        return {"ok": False, "status": "blocked", "network": False, "detail": "inventory path is forbidden"}
    text = _inventory_text(root)
    offline = any(
        token in text
        for token in (
            "--offline",
            "--local-only",
            '"network_audit": false',
            '"network_audit":false',
            "不下载",
            "离线",
        )
    )
    if not offline:
        return {
            "ok": False,
            "status": "blocked",
            "network": False,
            "detail": "no offline declaration; refusing to execute inventory in case it networks",
        }
    return {
        "ok": True,
        "status": "ready",
        "network": False,
        "detail": "offline mode declared; not executed by this check",
    }
