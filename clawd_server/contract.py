"""Frozen panel compatibility contract for clawd_server.

Runtime facts for sessions, runs, events, jobs and model options come from
gateway.platforms.api_server. This process does not keep a second run state
machine. The legacy panel auto-continue thread is disabled; scheduling belongs
to gateway/clawd_continuation.py when the core agent exports it.
"""

from __future__ import annotations

# Imported by tests so the contract cannot drift silently.
ENTRY = "clawd_server.__main__.main"
PREFIXES = ("/panel-api/", "/panel-core/", "/panel-auth/", "/")
COOKIE = "hp_auth"
AUTH_SALT = "hermes-panel:v2:"
APPROVAL_KEYS = ("request_id", "execution_generation", "choice")
SSE_CURSOR = "last_seq"
BIND_DEFAULT = "127.0.0.1"
CONTINUATION = "gateway.clawd_continuation"
