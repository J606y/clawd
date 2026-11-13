"""Retired ownership plugin.

Browser tools register targets they create. This copy no longer rewrites
``browser_exec``, no longer computes its own task id, and no longer opens a
shadow database. It only blocks browser tools that still have no ownership
check, so they cannot touch a shared browser's user pages.
"""

from __future__ import annotations

from typing import Any

# browser_vault is one unified tool (action=list|fill|save_login|enter_code|unlock).
_ALLOWED = frozenset({
    "browser_exec",
    "browser_cdp",
    "browser_dialog",
    "browser_vault",
})


def register(ctx: Any) -> None:
    def on_pre_tool_call(**kwargs: Any) -> dict[str, Any] | None:
        tool_name = str(kwargs.get("tool_name") or "")
        args = kwargs.get("args") or {}
        # browser_vault is one unified tool; every action is allowed (see _ALLOWED).
        if tool_name == "browser_vault" and isinstance(args, dict):
            return None
        untracked = (
            tool_name.startswith("mcp__chrome_devtools")
            or "playwright" in tool_name.lower()
            or (tool_name.startswith("browser_") and tool_name not in _ALLOWED)
        )
        if not untracked:
            return None
        return {
            "action": "block",
            "message": (
                f"Tool '{tool_name}' is blocked: it does not register the targets it creates. "
                "Use browser_exec."
            ),
        }

    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_tool_call", on_pre_tool_call)
