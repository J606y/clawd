"""The browser vault's prompts (unlock, save login, one-time code) for a viewer's turn.

Main's TUI sidecar registered the three vault callbacks per session (``tui_gateway/agent_callbacks.py``):
``browser_vault`` asked the renderer through ``vault.unlock_prompt``, ``vault.save_login`` and ``vault.code``
server→client requests, and the Desktop and Ink cards answered with ``{value}``. On the session authority no
callback was registered, so every Desktop chat reported ``prompt_unavailable`` ("this session cannot ask the
user") and a site's emailed or texted code could not be entered at all. Here the owner sends the same request
frames (``tui_gateway/contracts/server_requests.py``) to the viewers attached to the session's event stream and
blocks the agent thread until one answers, the turn is interrupted, or it times out. Passwords and codes are
never published, journaled or logged: they travel only in the viewer's response frame. The request registry
itself is ``gateway/session_server_requests.py``.

Panel runs (``v1/runs``) use the same three callbacks through a different transport: the prompt is
registered on ``PendingControls`` and forwarded to the run event stream as ``vault.request``, and the panel
card answers through ``POST /v1/runs/{run_id}/vault``. The ``ask_viewer`` frames never reach the panel —
it only sees run events — so the two branches below share the callback shapes but not the wire.
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

# Main's sidecar deadlines (``tui_gateway/agent_callbacks.py``).
UNLOCK_TIMEOUT_S = 120
SAVE_LOGIN_TIMEOUT_S = 180
CODE_TIMEOUT_S = 180


def release_unlocks(session_id) -> None:
    """The session closed: lock the password managers it unlocked, as the sidecar's session end does
    (``tui_gateway/session_lifecycle.py`` ``_lock_vault_managers``). Other sessions keep theirs."""
    try:
        from agent.vault_backends import unlock
        unlock.release_session(session_id)
    except Exception:  # health: allow BLE001 -- a close must finish even if the lock fails
        logger.debug("vault manager lock on session close failed", exc_info=True)


def _run_vault_listener_live(authority, session_id) -> bool:
    """True only when a ``v1/runs`` bridge is projecting this session's control prompts.

    ``observe_api_controls`` marks its observer ``vault_sink_kind='run'`` for the runs bridge
    (``api_server_runs._execute_run``); the chat/stream surface leaves the default ``'stream'``,
    whose sink drops everything but ``approval.request``. Without a run listener nobody can
    answer, so the callback returns "" immediately instead of stalling the turn for the
    full timeout. Fail-closed: any lookup failure means "no listener".
    """
    try:
        live = authority.sessions.get(session_id)
        if live is None:
            return False
        return any(getattr(o, "vault_sink_kind", None) == "run"
                   for o in live.event_stream.observers)
    except Exception:
        return False


@contextlib.contextmanager
def _api_vault_prompt_scope(turn, agent):
    """Install the vault callbacks for a panel run (``v1/runs`` turn).

    Each callback registers a vault prompt on ``PendingControls`` (which publishes
    ``vault.request`` to the run event stream) and blocks the agent thread on a
    ``threading.Event`` until the panel answers, the turn is interrupted, or the
    timeout elapses. The answer travels only through the responder — the secret is
    never published, journaled, or logged. On timeout/interrupt the prompt is
    discarded so the panel clears the card.

    The callbacks carry a ``_vault_surface = "panel-run"`` tag: ``can_prompt_here``
    uses it to tell a panel run (a human can answer) from cron/webhook/single-query
    turns (which never install a callback) and from the CLI ``-q`` callback
    (which carries no tag). Headless turns never enter this scope at all.
    """
    from agent.vault_backends import unlock

    try:
        authority, session_id, generation = turn._approval_owner
    except Exception:
        yield
        return
    live_prompts = []

    def ask_run(method, params, timeout):
        if not _run_vault_listener_live(authority, session_id):
            return ""
        done = threading.Event()
        holder = {}

        def responder(kind, prompt_id, answer):
            holder["answer"] = answer if isinstance(answer, str) else ""
            done.set()

        try:
            prompt_id = authority.register_vault(session_id, generation, method, params,
                                                 timeout_s=timeout, responder=responder)
        except Exception:
            logger.debug("vault prompt registration failed", exc_info=True)
            return ""
        live_prompts.append(prompt_id)
        deadline = time.monotonic() + timeout
        try:
            while not done.wait(0.2):
                if getattr(agent, "_interrupt_requested", False):
                    break
                if time.monotonic() >= deadline:
                    break
        finally:
            if not done.is_set():
                try:
                    authority.discard_vault(session_id, prompt_id)
                except Exception:
                    logger.debug("vault prompt discard failed", exc_info=True)
            with contextlib.suppress(ValueError):
                live_prompts.remove(prompt_id)
        return holder.get("answer", "")

    def unlock_prompt(backend, display_name):
        return ask_run("vault.unlock_prompt", {"backend": backend, "display_name": display_name},
                       UNLOCK_TIMEOUT_S)

    def save_login(origin, site):
        # The card sends identifier + password as one JSON string; it goes straight to the vault store.
        raw = ask_run("vault.save_login", {"origin": origin, "site": site}, SAVE_LOGIN_TIMEOUT_S)
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            return None
        return data if isinstance(data, dict) and data.get("password") else None

    def code_prompt(site, hint):
        return ask_run("vault.code", {"site": site, "hint": hint}, CODE_TIMEOUT_S)

    for callback in (unlock_prompt, save_login, code_prompt):
        # Tag read by can_prompt_here to tell a panel run from headless turns.
        callback._vault_surface = "panel-run"

    previous = (unlock.get_unlock_prompt_callback(), unlock.get_save_login_prompt_callback(),
                unlock.get_code_prompt_callback(), unlock.get_current_session_id())
    unlock.set_unlock_prompt_callback(unlock_prompt)
    unlock.set_save_login_prompt_callback(save_login)
    unlock.set_code_prompt_callback(code_prompt)
    unlock.set_current_session_id(session_id)  # an unlock made on this turn is released when this session closes
    try:
        yield
    finally:
        unlock.set_unlock_prompt_callback(previous[0])
        unlock.set_save_login_prompt_callback(previous[1])
        unlock.set_code_prompt_callback(previous[2])
        unlock.set_current_session_id(previous[3])
        for prompt_id in live_prompts[:]:
            try:
                authority.discard_vault(session_id, prompt_id)
            except Exception:
                logger.debug("vault prompt discard on scope exit failed", exc_info=True)


def _is_api_server_turn(turn) -> bool:
    """True for a turn whose source platform is the API server (panel runs, chat
    completions, …). Only panel runs ever get the vault callbacks — the scope
    additionally requires a live run listener — but the platform check keeps the
    headless rule explicit: cron/webhook/api single-query turns stay locked."""
    try:
        from gateway.config import Platform
        source = getattr(getattr(turn, "_ctx", None), "source", None)
        return getattr(source, "platform", None) == Platform.API_SERVER
    except Exception:
        return False


@contextlib.contextmanager
def vault_prompt_scope(turn, agent):
    """Install the vault callbacks on this (agent) thread for a viewer's turn; restore after.
    Tool workers inherit them through ``tools.thread_context.propagate_context_to_thread``.

    Local viewers (``local_viewer_turn``) keep the existing ``ask_viewer`` path. Panel runs
    get the ``PendingControls`` / run-event path. Every other context (cron, webhook,
    message gateways, single-query) gets no callback and the vault stays locked.
    """
    from gateway.run_turn_progress import local_viewer_turn
    if local_viewer_turn(turn):
        from agent.vault_backends import unlock
        authority, session_id, _generation = turn._approval_owner

        def ask(method, params, timeout):
            from gateway.session_server_requests import ask_viewer
            live = authority.sessions.get(session_id)
            if live is None:
                return ""
            return ask_viewer(live.event_stream, session_id, method, params, timeout=timeout,
                              cancelled=lambda: bool(getattr(agent, "_interrupt_requested", False)))

        def unlock_prompt(backend, display_name):
            return ask("vault.unlock_prompt", {"backend": backend, "display_name": display_name}, UNLOCK_TIMEOUT_S)

        def save_login(origin, site):
            # The card sends identifier + password as one JSON string; it goes straight to the vault store.
            raw = ask("vault.save_login", {"origin": origin, "site": site}, SAVE_LOGIN_TIMEOUT_S)
            try:
                data = json.loads(raw) if raw else None
            except ValueError:
                return None
            return data if isinstance(data, dict) and data.get("password") else None

        def code_prompt(site, hint):
            return ask("vault.code", {"site": site, "hint": hint}, CODE_TIMEOUT_S)

        previous = (unlock.get_unlock_prompt_callback(), unlock.get_save_login_prompt_callback(),
                    unlock.get_code_prompt_callback(), unlock.get_current_session_id())
        unlock.set_unlock_prompt_callback(unlock_prompt)
        unlock.set_save_login_prompt_callback(save_login)
        unlock.set_code_prompt_callback(code_prompt)
        unlock.set_current_session_id(session_id)  # an unlock made on this turn is released when this session closes
        try:
            yield
        finally:
            unlock.set_unlock_prompt_callback(previous[0])
            unlock.set_save_login_prompt_callback(previous[1])
            unlock.set_code_prompt_callback(previous[2])
            unlock.set_current_session_id(previous[3])
        return
    if _is_api_server_turn(turn):
        with _api_vault_prompt_scope(turn, agent):
            yield
        return
    yield
