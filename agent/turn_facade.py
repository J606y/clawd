"""``AIAgent.run_conversation`` / ``chat`` façade.

Turn admission around ``conversation_loop.run_conversation``: durable cross-process session turn lease +
refresher thread and liveness watchdog (``agent.turn_facade_lease``), relay/accounting/portal scopes, and
balanced start/finish marks. Extracted from ``run_agent.py``; every method resolves through ``AIAgent``'s
MRO unchanged.
"""
import logging
import uuid
from contextlib import suppress
from typing import Any, Dict, Generator, List, Optional

from agent.lazy_forward import forward as _forward

# Same logger name as the origin module so log records / caplog filters are unchanged.
logger = logging.getLogger("run_agent")


def _proven_session_home(agent):
    """Profile home proven by this agent's session database, or None.

    ``state.db``'s parent is the only proof. A missing path, a path that does
    not resolve, a previously frozen home, and the process environment are not
    proof. This never calls ``get_hermes_home()``.
    """
    from pathlib import Path

    db = getattr(agent, "_session_db", None)
    db_path = getattr(db, "db_path", None) if db is not None else None
    if not isinstance(db_path, (str, Path)) or not str(db_path).strip():
        return None
    try:
        from clawd_projects.inheritance import state_db_profile_home

        home = state_db_profile_home(Path(db_path))
    except Exception:
        logger.debug("session db path is not a profile home", exc_info=True)
        return None
    if not isinstance(home, Path) or not str(home):
        return None
    return home


def _homes_match(freeze, home) -> bool:
    recorded = getattr(freeze, "hermes_home", None) if freeze is not None else None
    if not recorded or home is None:
        return True
    from pathlib import Path

    from hermes_constants import hermes_home_key

    return hermes_home_key(Path(recorded)) == hermes_home_key(Path(home))


def _global_memory_store(store):
    """Innermost non-composite store. Re-attach must not nest the previous project."""
    seen: set[int] = set()
    current = store
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        inner = getattr(current, "global_store", None)
        if inner is None or inner is current:
            return current
        current = inner
    return store


def _store_project_id(store):
    if store is None or not hasattr(store, "project_store"):
        return None
    project_id = getattr(store, "project_id", None)
    return str(project_id) if project_id else None


def _nested_project_store(store) -> bool:
    inner = getattr(store, "global_store", None) if store is not None else None
    return inner is not None and inner is not store and _store_project_id(inner) is not None


def _capture_memory_scope(agent) -> dict:
    return {
        "store": getattr(agent, "_memory_store", None),
        "freeze": getattr(agent, "_memory_scope_freeze", None),
        "project_id": getattr(agent, "project_id", None),
        "project": getattr(agent, "project", None),
        "prompt": getattr(agent, "_cached_system_prompt", None),
        "static": getattr(agent, "_cached_system_prompt_static", None),
    }


def _restore_memory_scope(agent, saved: dict) -> None:
    agent._memory_store = saved["store"]
    agent._memory_scope_freeze = saved["freeze"]
    agent.project_id = saved["project_id"]
    agent.project = saved["project"]
    agent._cached_system_prompt = saved["prompt"]
    agent._cached_system_prompt_static = saved["static"]


def _rebuild_idle_prompt(agent) -> None:
    """Install the new scope's prompt bytes before the loop can restore the old row.

    Clearing the cache is not enough: a continuing session restores the stored
    prompt when the cache is missing. The rebuilt bytes stay on the agent so
    ``build_turn_context`` does not replace them, and a later fresh agent
    restores the same bytes.
    """
    try:
        from agent.system_prompt import invalidate_system_prompt

        invalidate_system_prompt(agent)
    except Exception:
        logger.debug("idle prompt invalidate failed", exc_info=True)
        agent._cached_system_prompt = None
        agent._cached_system_prompt_static = None
    builder = getattr(agent, "_build_system_prompt", None)
    if callable(builder):
        prompt = builder(None)
    else:
        from agent.system_prompt import build_system_prompt

        prompt = build_system_prompt(agent)
    if not isinstance(prompt, str):
        raise RuntimeError("idle project rebind did not produce a system prompt")
    agent._cached_system_prompt = prompt
    if getattr(agent, "_persist_disabled", False):
        return
    db = getattr(agent, "_session_db", None)
    session_id = getattr(agent, "session_id", None)
    updater = getattr(db, "update_system_prompt", None)
    getter = getattr(db, "get_session", None)
    if not callable(updater) or not callable(getter) or not session_id:
        return
    try:
        if getter(session_id) is None:
            return
        updater(session_id, prompt)
    except Exception:
        logger.warning(
            "idle project rebind could not persist the system prompt for session=%s; "
            "this turn keeps the rebuilt bytes, a later fresh agent may restore the previous row",
            session_id,
            exc_info=True,
        )
    try:
        from tools.mcp_tool_agent import persist_agent_tool_names

        persist_agent_tool_names(agent)
    except Exception:
        logger.debug("idle project rebind did not persist tool names", exc_info=True)


def _install_idle_memory_scope(agent, *, wanted, readonly: bool) -> None:
    """Point a reused agent at the current binding without nesting project stores.

    Unwrap a previous composite before re-attach so the old project cannot
    become the new store's global memory. ``_attach_project_context`` strips an
    explicit unbind itself; this pass still verifies that no project store or
    ``project_id`` remains. This does not walk parent sessions.
    """
    from agent.agent_init import _attach_project_context
    from clawd_projects.memory_authorization import (
        is_subagent_memory_actor,
        mark_memory_store_readonly,
    )

    current = getattr(agent, "_memory_store", None)
    if _store_project_id(current) is not None or _nested_project_store(current):
        agent._memory_store = _global_memory_store(current)
    _attach_project_context(agent, {}, memory_required=wanted is not None)
    installed = getattr(agent, "_memory_scope_freeze", None)
    installed_pid = getattr(installed, "project_id", None) if installed is not None else None
    if installed_pid != wanted:
        raise RuntimeError("idle rebind did not install the current project binding")
    if wanted is None:
        agent._memory_store = _global_memory_store(getattr(agent, "_memory_store", None))
        agent.project_id = None
        agent.project = None
        if _store_project_id(getattr(agent, "_memory_store", None)) is not None:
            raise RuntimeError("explicit unbind left a project memory store installed")
    if readonly or is_subagent_memory_actor(agent):
        mark_memory_store_readonly(getattr(agent, "_memory_store", None))
        if installed is not None and installed.writable is not False:
            from pathlib import Path

            from clawd_projects.memory_authorization import freeze_agent_memory_scope

            freeze_agent_memory_scope(
                agent,
                home=Path(installed.hermes_home),
                session_id=installed.session_id,
                project_id=installed.project_id,
                writable=False,
            )
    from tools.memory_tool import apply_memory_schema_to_agent

    apply_memory_schema_to_agent(agent)
    _rebuild_idle_prompt(agent)


def _project_scope_risk(agent) -> bool:
    """True when continuing could keep or apply an unproven project scope."""
    store = getattr(agent, "_memory_store", None)
    if _store_project_id(store) is not None or _nested_project_store(store):
        return True
    if getattr(agent, "project_id", None):
        return True
    if getattr(agent, "project", None) is not None:
        return True
    freeze = getattr(agent, "_memory_scope_freeze", None)
    return bool(getattr(freeze, "project_id", None))


def _refuse_unproven_project_scope(agent, reason: str) -> None:
    """Fail closed only when project memory is at risk. Ordinary global continues."""
    if not _project_scope_risk(agent):
        return
    raise RuntimeError(
        "idle project memory refused: "
        f"{reason}; refusing to guess a profile home or continue with an unproven project scope"
    )


def _sealed_freeze_matches(agent, home, freeze, frozen) -> bool:
    """A missing projects.db is not an unbind when this freeze still matches the store."""
    if not frozen or freeze is None or not _homes_match(freeze, home):
        return False
    store = getattr(agent, "_memory_store", None)
    if _nested_project_store(store) or _store_project_id(store) != str(frozen):
        return False
    agent_pid = getattr(agent, "project_id", None)
    if agent_pid not in (None, "") and str(agent_pid) != str(frozen):
        return False
    project = getattr(agent, "project", None)
    project_obj_id = getattr(project, "id", None) if project is not None else None
    if project_obj_id not in (None, "") and str(project_obj_id) != str(frozen):
        return False
    return True


def _keep_or_refuse_without_projects_db(agent, home, freeze, frozen) -> None:
    if _sealed_freeze_matches(agent, home, freeze, frozen):
        logger.debug("idle project resolve skipped; %s has no projects.db", home)
        return
    if _project_scope_risk(agent):
        raise RuntimeError(
            "projects.db is missing and project memory is not a sealed matching freeze; "
            "refusing to keep an unproven project scope or treat the missing file as an unbind"
        )


def _idle_project_memory_retarget(agent) -> None:
    """Refresh a reused agent's project memory before the next turn id and prompt snapshot.

    Called only from an idle facade entry, after that entry has been admitted and
    before ``conversation_loop`` assigns ``_current_turn_id`` or restores the
    system prompt. A live facade entry is not retargeted and its turn id is not
    cleared. The profile is the session database path, never the process home.
    Ordinary global mode is not blocked when that path cannot be proven and no
    project store is installed. An unproven project scope, a failed resolve, or
    an unfrozen composite without ``projects.db`` refuses the turn. An explicit
    unbind does not copy an ancestor. A child stays read-only.
    """
    if getattr(agent, "_turn_facade_entered", False):
        return
    # The previous facade turn has returned. Its leftover id would make
    # ``_attach_project_context`` keep the old store. This is not a live turn.
    if str(getattr(agent, "_current_turn_id", "") or ""):
        agent._current_turn_id = ""
    session_id = getattr(agent, "session_id", None)
    if not session_id:
        _refuse_unproven_project_scope(agent, "session id is missing")
        return
    home = _proven_session_home(agent)
    if home is None:
        _refuse_unproven_project_scope(agent, "session identity is not proven")
        return
    from pathlib import Path

    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    projects_db = Path(home) / "projects.db"
    freeze = getattr(agent, "_memory_scope_freeze", None)
    frozen = getattr(freeze, "project_id", None) if freeze is not None else None
    if not projects_db.is_file():
        _keep_or_refuse_without_projects_db(agent, home, freeze, frozen)
        return
    token = set_hermes_home_override(str(home))
    try:
        _retarget_idle_memory(agent, str(session_id), home, freeze, frozen)
    finally:
        reset_hermes_home_override(token)


def _retarget_idle_memory(agent, session_id: str, home, freeze, frozen) -> None:
    from clawd_projects.memory_authorization import is_subagent_memory_actor
    from clawd_projects.service import resolve_session_project

    try:
        project = resolve_session_project(session_id, hermes_home=home)
    except Exception as exc:
        raise RuntimeError(
            "idle project resolve failed; refusing to continue with the previous project scope"
        ) from exc
    wanted = None if project is None else getattr(project, "id", None)
    store = getattr(agent, "_memory_store", None)
    homes_match = _homes_match(freeze, home)
    store_pid = _store_project_id(store)
    store_matches = store_pid == wanted
    nested = _nested_project_store(store)
    if (
        freeze is not None
        and frozen == wanted
        and homes_match
        and store_matches
        and not nested
        and (store is not None or wanted is None)
    ):
        return
    project_change = frozen != wanted or not homes_match or not store_matches or nested
    if not project_change:
        # Already global, with nothing to seal or a seal that must not block the turn.
        if freeze is not None:
            return
        try:
            from agent.agent_init import _attach_project_context

            _attach_project_context(agent, {}, memory_required=False)
        except Exception:
            logger.debug("idle memory seal failed; keeping the current global scope", exc_info=True)
        return
    saved = _capture_memory_scope(agent)
    try:
        _install_idle_memory_scope(
            agent,
            wanted=wanted,
            readonly=is_subagent_memory_actor(agent),
        )
    except Exception:
        _restore_memory_scope(agent, saved)
        logger.debug("idle project rebind failed; restored the previous scope and refusing the turn", exc_info=True)
        raise


class TurnFacadeMixin:
    """run_conversation()/chat() (see module docstring)."""

    def run_conversation(
        self, user_message: Any, system_message: str | None=None,
        conversation_history: list[dict[str, Any]] | None=None, task_id: str | None=None,
        stream_callback: Optional[callable]=None, persist_user_message: Optional[Any]=None,
        persist_user_timestamp: Optional[float]=None, persist_user_display_kind: Optional[str]=None,
        persist_user_display_metadata: Optional[dict[str, Any]]=None,
        persist_user_platform_id: Optional[str]=None, moa_config: Optional[dict[str, Any]]=None,
        turn_author: Optional[dict[str, Any]] = None,
        relay_metadata: Optional[dict[str, Any]] = None,
        title_user_message: Optional[str]=None,
        prelude: Optional[Generator]=None,
    ) -> dict[str, Any]:
        """Forwarder — see ``agent.conversation_loop.run_conversation``."""
        # A review shares this session_id for cache parity: fence review startup or interrupt
        # an admitted request and await its exit before opening live-turn instrumentation.
        # Foreground priority is retained if the review does not acknowledge within the bounded deadline
        # (#84423).
        from agent.background_review import cancel_background_review_for_live_turn

        cancel_background_review_for_live_turn(self)

        from agent import relay_runtime
        from agent.aux_accounting import reset_accounting_context, set_accounting_context
        from agent.auxiliary_client import scoped_runtime_main
        from agent.conversation_loop import run_conversation
        from agent.portal_tags import (
            reset_affinity_scope, reset_conversation_context, set_affinity_scope,
            set_conversation_context,
        )
        from agent.prompt_cache_scope import declared_conversation_scope_safe
        from agent.relay_cwd import resolve_relay_scope_cwds
        from agent.review_idle_queue import QUEUE as _review_queue
        from agent.subagent_lifecycle import bind_subagent_parent
        from agent.interrupt_scope import track_in_interrupt_scope
        from agent.turn_facade_lease import admit_durable_turn_lease, carry_unadmitted_user_message
        from hermes_cli.observability.relay_shared_metrics import finish_task_run, start_task_run

        effective_task_id = task_id or str(uuid.uuid4())
        session_id = str(getattr(self, "session_id", None) or "")
        task_context = {
            "session_id": session_id,
            "task_id": effective_task_id,
            "platform": getattr(self, "platform", None) or "",
        }
        relay_turn_id = f"{session_id or 'session'}:{effective_task_id}:{uuid.uuid4().hex[:8]}"
        self._relay_pending_turn_id = relay_turn_id
        relay_parent_session_id = (
            str(getattr(self, "_parent_session_id", None) or "")
            if task_context["platform"] == "subagent"
            else ""
        )
        relay_lease = relay_turn = lease = None
        # Scope tokens start None: early returns leave the try before the set_*() calls and
        # the finally resets each one unconditionally.
        token = affinity_token = acct_token = None
        task_started = task_finished = False
        relay_outcome = "failed"
        facade_owner = not getattr(self, "_turn_facade_entered", False)

        try:
            # First statement of the try so the finally's note_turn_finished balances every exit.
            _review_queue.note_turn_started()
            admission = admit_durable_turn_lease(
                self, session_id=session_id, relay_turn_id=relay_turn_id, task_context=task_context,
                conversation_history=conversation_history,
            )
            if admission.early_result is not None:
                carry_unadmitted_user_message(
                    admission.early_result, user_message, persist_user_message,
                    timestamp=persist_user_timestamp, display_kind=persist_user_display_kind,
                    display_metadata=persist_user_display_metadata, platform_id=persist_user_platform_id,
                )
                relay_outcome = (
                    "cancelled" if admission.early_result.get("interrupted") else "timed_out"
                )
                return admission.early_result
            lease = admission.lease
            conversation_history = admission.conversation_history
            # Reused agents keep the previous turn id. Refresh project memory
            # and the prompt bytes before conversation_loop assigns this turn's
            # id and before it can restore the stored prompt. A nested entry
            # must not clear the outer turn's freeze or its entered flag.
            if facade_owner:
                _idle_project_memory_retarget(self)
                self._turn_facade_entered = True

            relay_session_cwd, relay_turn_cwd = resolve_relay_scope_cwds(
                self,
                effective_task_id,
                task_context["session_id"],
                task_context["platform"],
            )
            relay_lease = relay_runtime.SESSION_COORDINATOR.acquire_conversation(
                profile_key=relay_runtime.current_profile_key(),
                session_id=task_context["session_id"], platform=task_context["platform"],
                parent_session_id=relay_parent_session_id,
                model=str(getattr(self, "model", None) or ""),
                session_cwd=relay_session_cwd,
                turn_cwd=relay_turn_cwd,
            )
            relay_turn_kwargs: dict[str, Any] = {
                "turn_id": relay_turn_id,
                "task_id": effective_task_id,
            }
            if relay_metadata:
                relay_turn_kwargs["metadata"] = relay_metadata
            relay_turn = relay_runtime.SESSION_COORDINATOR.begin_turn(
                relay_lease, **relay_turn_kwargs
            )
            # Minimal relay-runtime shims may lack the opt-out flag: default enabled.
            if getattr(relay_turn, "relay_enabled", True):
                start_task_run(
                    **task_context,
                    parent_session_id=getattr(self, "_parent_session_id", None) or "",
                )
                task_started = True
            # Ambient Nous Portal tagging: every LLM call in this turn (loop, compression,
            # vision, MoA, review forks) inherits `conversation=<root>`; host-declared
            # affinity scope falls back to it; accounting handles route aux usage to the session.
            token = set_conversation_context(self._conversation_root_id())
            affinity_token = set_affinity_scope(declared_conversation_scope_safe(self))
            # Publish the session accounting handles the same way so auxiliary calls record their token
            # usage into session_model_usage (task dimension) — the fix for aux spend being invisible in
            # analytics (issue #23270).
            acct_token = set_accounting_context(
                getattr(self, "_session_db", None), getattr(self, "session_id", None)
            )

            # Keep the ContextVar scope local (agent tokens may be observed from another thread).
            # A host that owns this thread (Hermes Console) may cancel the turn cross-thread.
            with bind_subagent_parent(self), scoped_runtime_main({}), track_in_interrupt_scope(self):
                try:
                    if lease is not None:
                        lease.start()
                    result = run_conversation(
                        self, user_message, system_message, conversation_history, effective_task_id,
                        stream_callback, persist_user_message,
                        persist_user_timestamp=persist_user_timestamp,
                        persist_user_display_kind=persist_user_display_kind,
                        persist_user_display_metadata=persist_user_display_metadata,
                        persist_user_platform_id=persist_user_platform_id, moa_config=moa_config,
                        turn_author=turn_author,
                        title_user_message=title_user_message,
                        prelude=prelude,
                    )
                finally:
                    # Post-loop relay/task finalization must not receive a late refresh interrupt;
                    # the interrupt clear itself waits for the thread join in the outer finally.
                    if lease is not None:
                        lease.stop_refresher()
            terminal = result if isinstance(result, dict) else {}
            relay_outcome = (
                "cancelled" if terminal.get("interrupted") is True
                else "failed" if terminal.get("failed") is True
                else "success"
            )
            relay_runtime.SESSION_COORDINATOR.finish_logical_calls(relay_turn, outcome=relay_outcome)
            if task_started:
                task_finished = True
                finish_task_run(**task_context, result=result)
            return result
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, InterruptedError)) or (
                type(exc).__name__ == "CancelledError"
            ):
                relay_outcome = "cancelled"
            elif isinstance(exc, TimeoutError):
                relay_outcome = "timed_out"
            if relay_turn is not None:
                relay_runtime.SESSION_COORDINATOR.finish_logical_calls(
                    relay_turn, outcome=relay_outcome
                )
            if task_started and not task_finished:
                task_finished = True
                finish_task_run(**task_context, error=exc)
            raise
        finally:
            try:
                if relay_turn is not None:
                    relay_runtime.SESSION_COORDINATOR.end_turn(relay_turn, outcome=relay_outcome)
            finally:
                try:
                    if relay_lease is not None:
                        relay_runtime.SESSION_COORDINATOR.release_conversation(relay_lease)
                finally:
                    if lease is not None:
                        lease.stop_refresher()
                        lease.join_threads()
                        lease.clear_interrupt()  # refresher interrupt between stop and join; AFTER join
                        lease.release()
                    # Always clear mid-turn labels on exit — including interrupted early returns
                    # that skip finalize_turn. Keep ts.
                    with suppress(Exception):
                        self._reset_activity_labels_after_turn()
                    if getattr(self, "_relay_pending_turn_id", None) == relay_turn_id:
                        self._relay_pending_turn_id = None
                    if acct_token is not None:
                        reset_accounting_context(acct_token)
                    if token is not None:
                        reset_conversation_context(token)
                    if affinity_token is not None:
                        reset_affinity_scope(affinity_token)
                    # Balance note_turn_started so the idle queue's live-turn count cannot leak.
                    with suppress(Exception):
                        _review_queue.note_turn_finished()
                    if facade_owner:
                        self._turn_facade_entered = False

    def chat(self, message: str, stream_callback: Optional[callable] = None) -> str:
        """Final response string of one turn; ``stream_callback`` receives each text delta."""
        return self.run_conversation(message, stream_callback=stream_callback)["final_response"]

    _run_codex_app_server_turn = _forward("agent.codex_runtime", "run_codex_app_server_turn")
