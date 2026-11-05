"""Agent-facing tool: respond to a native JS dialog captured by the CDP supervisor.

Response-only: the agent calls ``browser_dialog(action=...)`` for a dialog the
supervisor holds; with several queued, the error names the candidate ids. Gated on ``_browser_cdp_check`` so it
appears together with ``browser_cdp``. Design: ``website/docs/developer-guide/browser-supervisor.md``.

A harness task dialog is answered only on the daemon socket that already holds
the signed URL, and only when that daemon supplied target, session, and frame
plus a matching task grant. This module does not open a websocket, does not
claim an unregistered page, and does not close a page.
"""

from __future__ import annotations

import hmac
import json
from typing import Any, Dict, Optional

from tools.browser_supervisor import SUPERVISOR_REGISTRY
from tools.registry import registry

BROWSER_DIALOG_SCHEMA: dict[str, Any] = {
    "name": "browser_dialog",
    "description": (
        "Accept or dismiss a native JS dialog (alert/confirm/prompt) blocking the page.\n\n"
        "**Workflow:** when a page is blocked by a native dialog the CDP "
        "supervisor captured, call this tool with ``action='accept'`` or "
        "``action='dismiss'``.\n\n"
        "**Prompt dialogs:** pass ``prompt_text`` to supply the response "
        "string. Ignored for alert/confirm/beforeunload.\n\n"
        "**Multiple dialogs:** if more than one dialog is queued (rare — "
        "happens when a second dialog fires while the first is still open), "
        "pass ``dialog_id`` (the error lists the candidates) to disambiguate.\n\n"
        "**Availability:** only present when a CDP endpoint is attached — "
        "a local Chromium-family browser via ``/browser connect``, or "
        "``browser.cdp_url`` in config.yaml."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["accept", "dismiss"],
                "description": (
                    "'accept' clicks OK / returns the prompt text. "
                    "'dismiss' clicks Cancel / returns null from prompt(). "
                    "For ``beforeunload`` dialogs: 'accept' allows the "
                    "navigation, 'dismiss' keeps the page."
                ),
            },
            "prompt_text": {
                "type": "string",
                "description": (
                    "Response string for a ``prompt()`` dialog. Ignored for "
                    "other dialog types. Defaults to empty string."
                ),
            },
            "dialog_id": {
                "type": "string",
                "description": (
                    "Specific dialog to respond to, from "
                    "the candidate ids a previous call listed. Required "
                    "only when multiple dialogs are queued."
                ),
            },
        },
        "required": ["action"],
    },
}


def _dialog_error(code: str, detail: str) -> str:
    return json.dumps({"success": False, "error": f"{code}: {detail}"})


def _harness_connection(task_id: Optional[str]) -> Optional[dict]:
    """Harness/cloud task locator, or None when this call is not that route."""
    if not task_id:
        return None
    from tools.browser_task_registry import task_connection

    conn = task_connection(task_id)
    if not conn:
        return None
    endpoint = str(conn.get("endpoint") or "")
    if conn.get("transport") != "harness" and endpoint != "harness" and not endpoint.startswith("wss://"):
        return None
    return conn


def _dialog_identity(raw: object) -> Optional[dict]:
    """Daemon dialog fields. None when target, session, or frame is missing."""
    if not isinstance(raw, dict):
        return None
    dialog = raw
    if "dialog" in raw and not any(key in raw for key in ("targetId", "target_id", "sessionId", "frameId")):
        inner = raw.get("dialog")
        if not isinstance(inner, dict):
            return None
        dialog = inner
    target_id = dialog.get("targetId") or dialog.get("target_id")
    session_id = dialog.get("sessionId") or dialog.get("session_id")
    frame_id = dialog.get("frameId") or dialog.get("frame_id")
    if not all(isinstance(value, str) and value for value in (target_id, session_id, frame_id)):
        return None
    grant = dialog.get("task_grant")
    named = dialog.get("dialogId") or dialog.get("dialog_id") or ""
    return {
        "targetId": target_id,
        "sessionId": session_id,
        "frameId": frame_id,
        "dialogId": named if isinstance(named, str) else "",
        "task_grant": grant if isinstance(grant, str) else "",
        "type": dialog.get("type") if isinstance(dialog.get("type"), str) else "",
        "message": dialog.get("message") if isinstance(dialog.get("message"), str) else "",
    }


def _grants_match(registry_grant: str, dialog_grant: str) -> bool:
    if not isinstance(registry_grant, str) or not registry_grant:
        return False
    if not dialog_grant:
        return True
    if len(dialog_grant) != len(registry_grant):
        return False
    return hmac.compare_digest(dialog_grant, registry_grant)


def _answer_harness_dialog(task_id: Optional[str], hermes_session: Optional[str], action: str,
                           prompt_text: Optional[str], dialog_id: Optional[str]) -> Optional[str]:
    """Answer a harness-task dialog, or None when this call is not a harness task.

    Returns JSON for every harness/cloud task, including failure. Does not open
    a websocket, does not fall back to the current page or VNC, and does not
    close a page. A missing target, session, frame, or matching grant fails closed.
    """
    conn = _harness_connection(task_id)
    if conn is None:
        return None
    unscoped = _dialog_error(
        "dialog_unscoped",
        "cloud dialog has no target, session, or frame id; a second websocket is not opened",
    )
    directory = str(conn.get("daemonDir") or "")
    stem = str(conn.get("daemonStem") or "")
    try:
        pid = int(conn.get("daemonPid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if not directory or not stem or not 0 < pid < (1 << 31):
        return unscoped
    if not hermes_session:
        return _dialog_error("dialog_unowned", "dialog is on a page this task created; session identity is required")
    if action not in {"accept", "dismiss"}:
        return unscoped
    from tools import browser_task_cdp as task_cdp
    from tools import browser_task_registry as task_registry

    pending_fn = getattr(task_cdp, "harness_pending_dialog", None)
    ensure = getattr(task_cdp, "ensure_harness_task_grants", None)
    grant_fn = getattr(task_registry, "harness_task_grant", None)
    exchange = getattr(task_cdp, "harness_exchange", None)
    if pending_fn is None or ensure is None or grant_fn is None or exchange is None:
        return unscoped
    try:
        ensure(directory, stem, pid)
        raw = pending_fn(directory, stem, pid)
    except Exception as exc:
        code = getattr(exc, "code", "")
        if code in {"task_grant_required", "task_grant_mismatch", "dialog_unowned"}:
            return _dialog_error("dialog_unowned", "dialog grant does not match this task")
        return unscoped
    dialog = _dialog_identity(raw)
    if dialog is None:
        return unscoped
    if dialog_id and dialog["dialogId"] != dialog_id:
        return _dialog_error("dialog_unscoped", "dialog_id is not the open dialog on this task page")
    if task_registry.active_binding(hermes_session, task_id, dialog["targetId"]) is None:
        return _dialog_error("dialog_unowned", "dialog is not on a page this task created")
    try:
        grant = grant_fn(dialog["targetId"], pid)
    except Exception:
        return _dialog_error("dialog_unowned", "dialog grant does not match this task")
    if not _grants_match(grant, dialog["task_grant"]):
        return _dialog_error("dialog_unowned", "dialog grant does not match this task")
    try:
        response = exchange(directory, stem, {
            "method": "Page.handleJavaScriptDialog",
            "params": {"accept": action == "accept", "promptText": prompt_text or ""},
            "session_id": dialog["sessionId"],
            "task_grant": grant,
        })
    except Exception:
        return _dialog_error("dialog_unowned", "dialog was not answered")
    if not isinstance(response, dict) or response.get("error"):
        code = response.get("error") if isinstance(response, dict) else ""
        if code not in {"task_grant_required", "task_grant_mismatch"}:
            code = "dialog_unowned"
        return _dialog_error(str(code), "dialog was not answered")
    return json.dumps({
        "success": True,
        "action": action,
        "dialog": {"type": dialog["type"], "message": dialog["message"], "targetId": dialog["targetId"],
                   "frameId": dialog["frameId"]},
    })


def browser_dialog(
    action: str,
    prompt_text: Optional[str] = None,
    dialog_id: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Respond to a pending dialog on the active task's CDP supervisor.

    A call that carries session identity may answer only a dialog whose CDP
    session or frame chain belongs to a page this task created. Calls without
    session identity keep the original supervisor response, except a dialog on
    a page this task created, which still requires that identity. A harness
    task is answered only through the daemon pending_dialog identity and grant,
    never on a supervisor socket. This function never closes a page.
    """
    if session_id is not None:
        from tools.browser_task_registry import IdentityError, resolve_call_identity
        try:
            task, session, _turn = resolve_call_identity(task_id, session_id)
        except IdentityError as exc:
            return json.dumps({"success": False, "error": f"browser_dialog refused: {exc}"})
        answered = _answer_harness_dialog(task, session, action, prompt_text, dialog_id)
        if answered is not None:
            return answered
        supervisor = SUPERVISOR_REGISTRY.get(task)
        if supervisor is None:
            return json.dumps({
                "success": False,
                "error": "No CDP supervisor is attached to this task. Call browser_exec first.",
            })
        pending = [item for item in supervisor.snapshot().pending_dialogs
                   if supervisor.dialog_is_owned(item, session, task)]
        if dialog_id:
            chosen = next((item for item in pending if item.id == dialog_id), None)
            if chosen is None:
                return json.dumps({
                    "success": False,
                    "error": f"dialog_id {dialog_id!r} is not an open dialog on a page this task created",
                })
        elif len(pending) != 1:
            return json.dumps({
                "success": False,
                "error": "no single dialog is open on a page this task created",
            })
        else:
            chosen = pending[0]
        dialog_id = chosen.id
    else:
        answered = _answer_harness_dialog(task_id, None, action, prompt_text, dialog_id)
        if answered is not None:
            return answered
        supervisor = SUPERVISOR_REGISTRY.get(task_id or "default")
        if supervisor is not None:
            pending = list(supervisor.snapshot().pending_dialogs)
            safe = [item for item in pending if not supervisor.dialog_on_created_page(item)]
            if pending and not safe:
                return json.dumps({
                    "success": False,
                    "error": "dialog is on a page this task created; session identity is required",
                })
            if dialog_id:
                chosen = next((item for item in safe if item.id == dialog_id), None)
                if chosen is None:
                    return json.dumps({
                        "success": False,
                        "error": "dialog_id is not an open dialog outside task pages",
                    })
                dialog_id = chosen.id
            elif len(safe) == 1:
                dialog_id = safe[0].id
            elif safe:
                return json.dumps({
                    "success": False,
                    "error": "multiple dialogs are open; specify dialog_id. Task-page dialogs require session identity.",
                })
    if supervisor is None:
        return json.dumps({
            "success": False,
            "error": (
                "No CDP supervisor is attached to this task: no browser "
                "session has been started yet. Call browser_exec or "
                "/browser connect first."
            ),
        })
    result = supervisor.respond_to_dialog(action=action, prompt_text=prompt_text, dialog_id=dialog_id)
    if result.get("ok"):
        return json.dumps({"success": True, "action": action, "dialog": result.get("dialog", {})})
    return json.dumps({"success": False, "error": result.get("error", "unknown error")})


def _browser_dialog_check() -> bool:
    """Gate: same as ``browser_cdp`` so the two tools appear/disappear together."""
    from tools.browser_cdp_tool import _browser_cdp_check

    return _browser_cdp_check()


registry.register(
    name="browser_dialog",
    toolset="browser-cdp",
    schema=BROWSER_DIALOG_SCHEMA,
    handler=lambda args, **kw: browser_dialog(action=args.get("action", ""), prompt_text=args.get("prompt_text"),
                                              dialog_id=args.get("dialog_id"), task_id=kw.get("task_id"),
                                              session_id=kw.get("session_id")),
    check_fn=_browser_dialog_check,
    emoji="💬",
)
