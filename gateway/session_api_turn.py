"""Trusted API preparation and observation of the canonical durable FIFO."""
import asyncio
from contextvars import ContextVar
from contextlib import contextmanager, asynccontextmanager
from functools import partial
import hmac
import json
import re
import uuid

from gateway.config import Platform
from gateway.session_api import bind_api_steps, restore_api_session, run_steps, run_steps_off_loop
from gateway.session_results import admission_result
from hermes_state_runtime import RuntimeStoreError, admit_session_input, _json

api_execution: ContextVar[dict | None] = ContextVar('api_execution', default=None)
# Set only for the admitted chat that just committed a tier, and only around
# ``_prepare_session_chat``. A new lock replace reads it so the persisted lock
# keeps that tier. It is not a profile guess and not a pending pin.
committed_effort_for_lock: ContextVar[str | None] = ContextVar('committed_effort_for_lock', default=None)
_SETTINGS_PREFIX = 'gateway.api.settings.v1.'
# Request-owned sinks an API observer may register for its admission's execution.
_OBSERVER_KEYS = ('stream_delta_callback', 'tool_start_callback', 'tool_complete_callback',
                  'reasoning_callback', 'status_callback', 'interim_assistant_callback')
_TOOL_SINKS = {'tool_start_callback', 'tool_complete_callback'}
_SETTING_KEYS = ('ephemeral_system_prompt', 'requested_model', 'requested_provider',
                 'model_options', 'route', 'session_model', 'confirmed_runtime_lock',
                 'requested_runtime', 'route_source', 'room_dispatch', 'room_execution_policy',
                 'session_history_delivery')
_OWNER_SCOPE_RE = re.compile(r'[0-9a-f]{64}')


class AdmissionQueued(RuntimeStoreError):
    """The owner retired while this API admission was still queued. Unlike a refused request, the
    input is committed: the row stays queued and the next owner runs it exactly once, so a client
    that resends it submits the work twice. ``str()`` is the message every API surface shows."""

    MESSAGE = 'clawd is restarting. Your input is saved and will run once when it is back; do not resend it.'

    def __init__(self):
        super().__init__('admission_queued')
        self.args = (self.MESSAGE,)


def api_settings(authority, ref):
    with authority.db._read_ctx() as conn:
        saved = conn.execute('SELECT value FROM state_meta WHERE key=?',
                             (_SETTINGS_PREFIX + ref.session_id,)).fetchone()
    return json.loads(saved[0]) if saved else {}


def check_api_turn(authority, ref, payload):
    live = authority.sessions[ref.session_id]
    if live.source.platform == Platform.API_SERVER:
        restore_api_session(authority, ref.session_id)
    adapter = authority.runner._adapter_for_source(live.source)
    if adapter is None or getattr(adapter, 'gateway_runner', None) is not authority.runner:
        raise RuntimeStoreError('runtime_draining')
    if 'api_turn_v1' in payload:
        data = payload['api_turn_v1']
        # 'browser_control' is accepted and ignored: admissions persisted while the removed
        # browser-extension control existed may carry it and must still recover.
        if (set(data) - {'history', 'settings', 'turn_author', 'media', 'run_owner_scope', 'browser_control',
                         'relay_metadata', 'resume_unanswered'}
                or data.get('resume_unanswered', True) is not True
                or not {'history', 'settings'} <= set(data)
                or not isinstance(data.get('relay_metadata', {}), dict)
                or (data['history'] is not None and not isinstance(data['history'], list))
                or ('run_owner_scope' in data and not _valid_owner_scope(data['run_owner_scope']))):
            raise RuntimeStoreError('invalid_params')
        if set(data['settings']) - set(_SETTING_KEYS):
            raise RuntimeStoreError('invalid_params')
    settings = payload.get('api_turn_v1', {}).get('settings') or api_settings(authority, ref)
    check_api_settings(adapter, settings)
    return adapter


def _valid_owner_scope(value):
    return isinstance(value, str) and _OWNER_SCOPE_RE.fullmatch(value) is not None


def api_session_vars():
    """Session-context fields an executing API admission binds beyond the shared source:
    #98619 history-delivery provenance. ``{}`` when no API admission is executing."""
    current = api_execution.get()
    if current is None:
        return {}
    return {'session_history_delivery': current['settings'].get('session_history_delivery') or ''}


def check_api_settings(adapter, settings):
    dispatch = settings.get('room_dispatch')
    # A room policy is only ever issued with the dispatch it was verified for; a policy-only
    # blob (e.g. queued by an older build) would otherwise widen the operator's API config.
    if dispatch is None and settings.get('room_execution_policy') is not None:
        raise RuntimeStoreError('permission_denied')
    if dispatch is not None:
        from gateway.hosted_room_peer import HostedMemberDispatch, GatewayRoomCatalog
        from gateway.platforms.api_server_room_grants import _local_room_catalog
        from gateway import hosted_rooms
        bound = HostedMemberDispatch.from_mapping(dispatch)
        if bound.target_install_id != hosted_rooms.local_authority_gateway_id():
            raise RuntimeStoreError('permission_denied')
        _, catalog = _local_room_catalog(adapter, bound.target_profile, bound.target_install_id)
        current = GatewayRoomCatalog.from_mapping(catalog)
        if (current.catalog_digest != bound.capability_digest
                or current.execution_policy.as_mapping() != settings.get('room_execution_policy')):
            raise RuntimeStoreError('permission_denied')
    return adapter


@contextmanager
def api_policy_scope():
    current = api_execution.get()
    policy = current['settings'].get('room_execution_policy') if current else None
    token = None
    if policy is not None:
        from gateway.hosted_room_execution_policy import RoomExecutionPolicy, bind_room_execution_policy
        token = bind_room_execution_policy(RoomExecutionPolicy.from_mapping(policy))
    try:
        yield
    finally:
        if token is not None:
            from gateway.hosted_room_execution_policy import reset_room_execution_policy
            reset_room_execution_policy(token)


def admit_api_turn(adapter, **kwargs):
    """Admit inline (sync callers and tests); the HTTP doors use ``admit_api_turn_async``."""
    return run_steps(admit_api_steps(adapter, **kwargs))


async def admit_api_turn_async(adapter, steps=None, *, orphaned=None, **kwargs):
    """``admit_api_turn`` (or *steps*, a generator ending in ``admit_api_steps``) with every SQLite
    write off the owner loop, in admission order. ``orphaned`` replaces the caller-cancel hook;
    None keeps ``schedule_orphan``."""
    from gateway.session_authorities import require_active_authority
    authority = require_active_authority(adapter.gateway_runner)
    return await run_steps_off_loop(
        authority, steps or admit_api_steps(adapter, **kwargs),
        orphaned=schedule_orphan if orphaned is None else orphaned)


def schedule_orphan(admitted):
    """A caller cancelled while its admission committed never observes it: its drain runs anyway."""
    authority, ref, row = admitted[:3]
    if row['status'] == 'queued':
        authority._schedule(ref)


def admit_api_steps(adapter, **kwargs):
    # ``/p/<profile>/`` middleware scoped this request; the routed home's authority admits it.
    from gateway.session_authorities import require_active_authority
    authority = require_active_authority(adapter.gateway_runner, lambda: adapter._ensure_session_db())
    sid = kwargs.get('session_id') or uuid.uuid4().hex
    declared_key = kwargs.get('gateway_session_key') if kwargs.get('bind_declared_conversation') else None
    if declared_key:
        from gateway.session_api import declared_api_session
        sid = declared_api_session(authority.db, declared_key) or sid
    # Every API door (chat header, runs session_id, /api/sessions/{id}/chat, Responses chain) may
    # name a compression continuation; the binding, FIFO and retry identity live on its root.
    sid = authority.logical_owner(sid)
    authority._require_admission_open()
    settings = {key: kwargs.get(key) for key in _SETTING_KEYS}
    # Route credentials remain in the server's configuration, never admission JSON.
    route = settings.get('route')
    if route and route.get('api_key'):
        alias = settings.get('requested_model')
        configured = adapter._model_routes.get(alias)
        if configured != route:
            raise RuntimeStoreError('permission_denied')
        settings['route'] = {k: v for k, v in route.items() if k != 'api_key'}
    payload = json.loads(_json({'text': kwargs['user_message'], 'api_turn_v1': {
        'history': None if kwargs.get('history_from_session') else kwargs['conversation_history'], 'settings': settings}}))
    run_owner_scope = kwargs.get('run_owner_scope')
    if run_owner_scope is not None:
        if not _valid_owner_scope(run_owner_scope):
            raise RuntimeStoreError('invalid_params')
        # This opaque namespace is persisted in the same row/transaction as
        # admission. It is never a bearer credential or execution input.
        payload['api_turn_v1']['run_owner_scope'] = run_owner_scope
    # Refuse a foreign-surface target before committing any media for it (a refused
    # admission otherwise retains its image bytes under native-inputs/ forever).
    existing = authority.db.get_session(sid)
    if existing is not None and existing['source'] not in ('api_server', 'bot_room'):
        raise RuntimeStoreError('permission_denied')
    if kwargs.get('turn_author') is not None:
        from agent.turn_author import parse_turn_author
        author = parse_turn_author(kwargs['turn_author'])
        if author is None:
            raise RuntimeStoreError('invalid_params')
        payload['api_turn_v1']['turn_author'] = author
    if kwargs.get('resume_unanswered_turn'):
        # The session-chat door's one policy-gated re-run (#115325): its failed attempt left this text as
        # the transcript's unanswered tail, which the turn adopts instead of appending it again.
        payload['api_turn_v1']['resume_unanswered'] = True
    if kwargs.get('relay_metadata'):
        # The request's Relay metadata rides the admission to the turn it describes (main passed it
        # to ``run_conversation``); it is client JSON, so it is part of the exact-retry identity.
        payload['api_turn_v1']['relay_metadata'] = kwargs['relay_metadata']
    request_id = kwargs.get('request_id') or kwargs.get('active_run_id') or uuid.uuid4().hex
    if not isinstance(kwargs['user_message'], list):
        return (yield from _admit_api_payload(authority, adapter, sid, request_id, payload, settings, declared_key, kwargs))
    from gateway.session_api_media import commit_api_images
    from gateway.session_ingress_media import capture_lease, drop_capture_lease, release_unheld_media
    # Content-addressed bytes another admission may name are held from capture until this one
    # commits or is refused: a concurrent release would otherwise unlink them in between.
    with capture_lease():
        payload['api_turn_v1']['media'] = commit_api_images(kwargs['user_message'])
        # Retained bytes belong to an accepted admission. A refused request (or an exact retry of a
        # retired one, whose references were erased) owns nothing, so its bytes are collected unless
        # another admission holds them.
        release = partial(release_unheld_media, authority.db, payload['api_turn_v1']['media'])
        try:
            admitted = yield from _admit_api_payload(authority, adapter, sid, request_id, payload, settings, declared_key, kwargs)
        except Exception:
            drop_capture_lease()
            yield release
            raise
    yield release
    return admitted


def _admit_api_payload(authority, adapter, sid, request_id, payload, settings, declared_key, kwargs):
    from hermes_state_terminal import retry_terminal_admission
    row = retry_terminal_admission(authority.db, epoch=authority.epoch, principal_id='api',
        session_id=sid, request_id=request_id, payload=payload)
    if row is not None:
        check_api_settings(adapter, settings)
        from gateway.session_contract import SessionRef
        return authority, SessionRef(authority.profile_id, sid), row
    ref = yield from bind_api_steps(authority, sid, hosted_dispatch=kwargs.get("room_dispatch"), declared_key=declared_key,
                                    caller_key=kwargs.get('gateway_session_key'), walked_from=kwargs.get('walked_from'))
    check_api_turn(authority, ref, payload)
    row = yield partial(admit_session_input, authority.db, epoch=authority.epoch, principal_id='api',
                        session_id=sid, request_id=request_id, payload=payload,
                        _authorize_write=authority._admission_gate(partial(
                            _admit_target, sid, request_id, payload['api_turn_v1']['settings'],
                            client_key=bool(kwargs.get('request_id')))))
    return authority, ref, row


def _admit_target(session_id, request_id, settings, conn, *, client_key=True):
    """Inside the admission's own off-loop transaction: one target per request id, and the
    session's settings for its later payload-less (internal) turns, committed with the admitted
    turn rather than by the drain on the owner loop (N50)."""
    _one_target(session_id, request_id, conn, client_key=client_key)
    conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (_SETTINGS_PREFIX + session_id, _json(settings)))


def _one_target(session_id, request_id, conn, *, client_key=True):
    """One API request id (an Idempotency-Key) names one admission. The ledger keys identity by
    target too, so the same key aimed at another session would otherwise run the work again.
    A deleted session's admission is gone from the ledger but its tombstone still spends the key
    (a target that already holds the key replays before this guard runs). Only a client key can
    name a tombstone: a server-minted id (key-less turn, run id) is fresh, so it skips that scan."""
    if conn.execute("SELECT 1 FROM session_admissions WHERE principal_id='api' AND request_id=? "
                    'AND target_session_id!=? LIMIT 1', (request_id, session_id)).fetchone():
        raise RuntimeStoreError('admission_conflict')
    if not client_key:
        return
    from hermes_state_terminal import terminal_api_admissions
    if any(row['target_session_id'] != session_id for row in terminal_api_admissions(conn, request_id)):
        raise RuntimeStoreError('admission_conflict')


def owns_api_run(adapter, run_id, owner_scope):
    """Match a caller scope against one canonical API admission, failing closed."""
    if not _valid_owner_scope(owner_scope):
        return False
    from gateway.platforms.api_server_authority_runs import run_admission
    try:
        owned = run_admission(adapter, run_id)
    except RuntimeStoreError:
        # Duplicate admissions for one run are an unanswered ownership question.
        return False
    if owned is None:
        return False
    try:
        stored = owned[1]['payload']['api_turn_v1']['run_owner_scope']
    except (KeyError, TypeError):
        return False
    return _valid_owner_scope(stored) and hmac.compare_digest(stored, owner_scope)


def recover_api_turns(adapter):
    """Recover committed work only after the real API adapter is published. A parked profile's
    retained (retiring) authority is not served: its store is never read here."""
    from gateway.session_authorities import owner_scope, serving_authorities
    for authority in serving_authorities(adapter.gateway_runner):
        # Startup is unscoped (the launch profile's home): each profile's media root, config and
        # API bindings must pair with its own state.db, never the launch profile's.
        with owner_scope(authority):
            recover_authority_api_turns(adapter, authority)


def recover_authority_api_turns(adapter, authority):
    from hermes_state_runtime import list_session_admissions
    from gateway.session_ingress_media import collect_unheld_api_images
    import logging
    collect_unheld_api_images(authority.db)
    from gateway.session_api_media import compact_settled_api_payloads
    compact_settled_api_payloads(authority.db)
    with authority.db._read_ctx() as conn:
        targets = [row[0] for row in conn.execute(
            "SELECT DISTINCT target_session_id FROM session_admissions WHERE principal_id='api' AND status='queued'")]
    for sid in targets:
        try:
            ref = restore_api_session(authority, sid)
            pending = list_session_admissions(authority.db, session_id=sid)
            if any(row['status'] == 'unknown' for row in pending):
                continue
            for row in pending:
                if row['status'] == 'queued':
                    check_api_turn(authority, ref, row['payload'])
            authority._schedule(ref)
        except RuntimeStoreError as exc:
            logging.getLogger(__name__).warning('API session %s paused: %s', sid, exc.reason)


def _retain_latched_gate(adapter, run_id, gate):
    """Restore a closed-over gate the stream finally already popped.

    A different object already in the registry is a newer identity and is left
    alone. The restored object keeps ``stop_requested`` after the pop.
    """
    pending = getattr(adapter, '_pending_run_cancels', None)
    if not isinstance(pending, dict) or not isinstance(gate, dict) or not run_id:
        return False
    current = pending.get(run_id)
    if current is not None:
        return False
    gate['_retained_after_pop'] = True
    pending[run_id] = gate
    return True


def _schedule_exact_cancel(adapter, run_id, admitted, gate=None):
    """Cancel the admission a cancelled caller already committed.

    The task holds *gate*, so the stream finally can pop the registry without
    dropping the latched stop. A schedule failure puts that object back.
    Background failure is logged; it is not discarded.
    """
    import logging
    log = logging.getLogger(__name__)
    if isinstance(gate, dict):
        try:
            gate['admitted'] = admitted
        except Exception:
            log.exception('exact admission cancel could not store identity for %s', run_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.error('exact admission cancel was not scheduled; no running loop for run %s', run_id)
        _retain_latched_gate(adapter, run_id, gate)
        return
    from gateway.platforms.api_server_authority_runs import cancel_exact_admission

    async def _cancel():
        outcome = await cancel_exact_admission(adapter, run_id, admitted)
        if outcome not in ('cancelled', 'stopping', 'settled'):
            raise RuntimeStoreError('admission_conflict')
        return outcome

    cancel_coro = _cancel()
    try:
        task = loop.create_task(cancel_coro)
    except Exception:
        cancel_coro.close()
        log.exception('exact admission cancel could not be scheduled for run %s', run_id)
        _retain_latched_gate(adapter, run_id, gate)
        return
    if gate is not None:
        try:
            task._clawd_latched_cancel_gate = gate
        except Exception:
            log.exception('exact admission cancel could not retain its gate for run %s', run_id)
    track = getattr(adapter, '_track_background_task', None)
    if track is None:
        log.error('exact admission cancel has no task tracker for run %s', run_id)
    else:
        try:
            track(task)
        except Exception:
            log.exception('exact admission cancel was not tracked for run %s', run_id)

    def _log_task_error(exc):
        log.error(
            'exact admission cancel failed for run %s: %s',
            run_id, getattr(exc, 'reason', None) or type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__))

    def _retrieve(done, _gate=gate, _run_id=run_id, _adapter=adapter):
        # Keep the gate object alive for this task even after the registry pop.
        if isinstance(_gate, dict):
            _gate.get('stop_requested')
        if done.cancelled():
            log.error('exact admission cancel task was cancelled for run %s', _run_id)
            _retain_latched_gate(_adapter, _run_id, _gate)
            return
        exc = done.exception()
        if exc is not None:
            _log_task_error(exc)
            _retain_latched_gate(_adapter, _run_id, _gate)
            return
        if isinstance(_gate, dict) and _gate.get('_retained_after_pop'):
            pending = getattr(_adapter, '_pending_run_cancels', None)
            if isinstance(pending, dict) and pending.get(_run_id) is _gate:
                pending.pop(_run_id, None)
            release = getattr(_adapter, '_release_run_owner_if_forgotten', None)
            if release is not None:
                try:
                    release(_run_id)
                except Exception:
                    log.exception('exact admission cancel could not release owner for %s', _run_id)

    try:
        task.add_done_callback(_retrieve)
    except Exception:
        log.exception('exact admission cancel result will be unobserved for run %s', run_id)
        if task.done() and not task.cancelled():
            exc = task.exception()
            if exc is not None:
                _log_task_error(exc)
        _retain_latched_gate(adapter, run_id, gate)


async def run_api_turn(adapter, *, approval_notify_callback=None, approval_session_key=None, **kwargs):
    run_key = kwargs.get('active_run_id') or approval_session_key
    gate = getattr(adapter, '_pending_run_cancels', {}).get(run_key) if run_key else None
    orphaned = None
    if gate is not None:
        gate['phase'] = 'admitting'

        def orphaned(admitted, _gate=gate, _adapter=adapter, _key=run_key):
            if _gate.get('stop_requested'):
                # Close over the gate object. The stream finally may already have
                # popped the registry; that pop must not drop this latch.
                _schedule_exact_cancel(_adapter, _key, admitted, _gate)
                return
            schedule_orphan(admitted)
    try:
        admitted = await admit_api_turn_async(adapter, orphaned=orphaned, **kwargs)
    except BaseException:
        raise
    else:
        if gate is not None:
            gate['phase'] = 'admitted'
            gate['admitted'] = admitted
    if gate is not None and gate.get('stop_requested') and run_key:
        from gateway.platforms.api_server_authority_runs import cancel_exact_admission
        import logging
        _cancel_log = logging.getLogger(__name__)
        try:
            outcome = await cancel_exact_admission(adapter, run_key, admitted)
        except RuntimeStoreError as exc:
            if exc.reason == 'unknown_execution':
                raise
            if exc.reason == 'not_found':
                # The committed row is gone. Do not replay the admit snapshot.
                _cancel_log.warning('exact admission cancel found no row for %s', run_key)
                raise RuntimeStoreError('unknown_execution') from exc
            if exc.reason in ('stale_generation', 'stale_epoch'):
                # Not this row's current generation or epoch. Observe the same
                # admission below; do not interrupt another epoch and do not
                # invent a cancelled result.
                _cancel_log.warning(
                    'exact admission cancel was not applied for %s: %s', run_key, exc.reason)
            else:
                _cancel_log.warning(
                    'exact admission cancel was not applied for %s: %s', run_key, exc.reason)
                raise
        else:
            if outcome not in ('cancelled', 'stopping', 'settled'):
                _cancel_log.error(
                    'exact admission cancel for %s returned %s', run_key, outcome)
                raise RuntimeStoreError('admission_conflict')
    if approval_notify_callback is None or not approval_session_key:
        return await observe_api_turn(admitted, **kwargs)
    # A streaming surface advertises its own run id (``chatcmpl-*`` / ``run_*``): bind it to this
    # exact admission so ``/v1/runs/{id}/approval`` answers the shared, generation-fenced prompt,
    # and hand that prompt to the stream's notifier in the event shape it already emits.
    aliases = adapter._run_admission_aliases
    aliases[approval_session_key] = admitted[2]['admission_id']

    def sink(event_type, prompt):
        if event_type == 'approval.request':
            choices = prompt.get('choices') or ()
            approval_notify_callback({
                **{k: v for k, v in prompt.items() if k not in ('kind', 'prompt_id', 'choices')},
                'request_id': prompt['prompt_id'], 'allow_session': 'session' in choices,
                'allow_permanent': 'always' in choices})
    try:
        with observe_api_controls(admitted, sink):
            return await observe_api_turn(admitted, **kwargs)
    finally:
        if aliases.get(approval_session_key) == admitted[2]['admission_id']:
            aliases.pop(approval_session_key)


async def observe_api_turn(admitted, **kwargs):
    authority, ref, row = admitted
    from hermes_state_runtime import get_session_admission
    # The admission-time snapshot is stale once a drain already running ahead claims and settles
    # (or a stop cancels) the row; a waiter registered after that is never resolved. The current
    # row decides, read with no suspension before the waiter is registered.
    from gateway.platforms.api_server_authority_runs import read_exact_admission
    try:
        current = read_exact_admission(admitted)
    except RuntimeStoreError as exc:
        if exc.reason == 'not_found':
            raise RuntimeStoreError('unknown_execution') from exc
        raise
    status = current['status']
    if status == 'unknown':
        raise RuntimeStoreError('unknown_execution')
    if status == 'terminal':
        result, usage = _settled_api_result(authority, row['admission_id'])
        callback = kwargs.get('stream_delta_callback')
        if callback:
            callback(result.get('final_response') or '')
        return result, usage
    waiter = authority.waiters.setdefault(row['admission_id'], asyncio.get_running_loop().create_future())
    observers = getattr(authority, 'api_observers', None)
    if observers is None:
        observers = authority.api_observers = {}
    observer = {key: kwargs[key] for key in _OBSERVER_KEYS if kwargs.get(key) is not None}
    if kwargs.get('tool_progress_callback') is not None and not observer.keys() & _TOOL_SINKS:
        # Progress-only (the session chat stream): the producer's own tool.started / tool.completed
        # events with its preview and failure verdict. Structured observers keep only theirs.
        observer['tool_progress_callback'] = kwargs['tool_progress_callback']
    registered = observers.setdefault(row['admission_id'], [])
    registered.append(observer)
    try:
        authority._publish_pending(ref)
        authority._schedule(ref)
        await asyncio.shield(waiter)
    except RuntimeStoreError as exc:
        # Retirement releases every observer with runtime_draining, the same reason a refused
        # request gets. This one's input is admitted: say so while the row is still queued.
        current = get_session_admission(authority.db, admission_id=row['admission_id']) or {}
        if exc.reason == 'runtime_draining' and current.get('status') == 'queued':
            raise AdmissionQueued() from exc
        raise
    finally:
        # Shielding keeps the canonical turn alive past a cancelled request; only this
        # request's observer leaves, and the entry itself goes once the last one is gone.
        registered.remove(observer)
        if not registered:
            observers.pop(row['admission_id'], None)
    return _settled_api_result(authority, row['admission_id'])


def _settled_api_result(authority, admission_id):
    saved = admission_result(authority.db, admission_id)
    if saved is None:
        from hermes_state_runtime import get_session_admission
        current = get_session_admission(authority.db, admission_id=admission_id)
        if (isinstance(current, dict) and current.get('admission_id') == admission_id
                and current.get('status') == 'terminal' and current.get('outcome') == 'cancelled'):
            return {'final_response': '', 'interrupted': True, 'completed': False}, {}
        raise RuntimeStoreError('unknown_execution')
    return saved['result'], saved['usage']


_CONTROL_EVENTS = frozenset({'approval.request', 'approval.settled', 'clarify.request', 'clarify.settled',
                             'vault.request', 'vault.settled'})


@contextmanager
def observe_api_controls(admitted, sink, *, sink_kind="stream"):
    """Project the same approval/clarify prompts WS viewers receive to ``sink(type, payload)``
    for the admission's lifetime; ``sink`` runs under the stream lock.

    An exact retry joins an admission whose prompt may already be pending: it was published
    before this observer existed, so it is replayed from the shared pending controls (the
    source ``GET /v1/runs/{id}`` reports). Subscribing and snapshotting in ONE stream-lock hold
    is the consistent cut: prompts are registered and answered under that lock, so each one is
    either in the snapshot or arrives live (never both), and one already answered is absent."""
    from hermes_state_runtime import get_session_admission
    authority, ref, row = admitted
    from gateway.session_retirement import restore_retired
    restore_retired(authority, ref.session_id)  # idle between its bind and this admission's commit
    live = authority.sessions.get(ref.session_id)
    if live is None:
        # Deleted or pruned: only an exact retry of a settled (retired) receipt reaches here,
        # and a settled admission has no prompts left to project.
        yield
        return
    events = live.event_stream

    def observer(frame):
        params = frame['params']
        if params.get('admission_id') == row['admission_id'] and params.get('type') in _CONTROL_EVENTS:
            sink(params['type'], params.get('payload') or {})

    # Runs bridge passes sink_kind="run"; the chat/stream surface leaves "stream".
    # ``session_vault_prompt`` reads this to decide whether a panel card can answer.
    observer.vault_sink_kind = sink_kind
    with events.lock:
        events.observers.add(observer)
        current = get_session_admission(authority.db, admission_id=row['admission_id'])
        generation = current.get('generation') if isinstance(current, dict) else None
        if (isinstance(current, dict) and current.get('admission_id') == row.get('admission_id')
                and current.get('target_session_id') == ref.session_id
                and current.get('status') == 'started' and type(generation) is int):
            for prompt in live.controls.snapshot(ref.session_id, generation):
                sink(prompt['kind'] + '.request', prompt)
    try:
        yield
    finally:
        with events.lock:
            events.observers.discard(observer)


def prepare_api_execution(authority, ref, payload):
    adapter = check_api_turn(authority, ref, payload)
    data = payload.get('api_turn_v1')
    settings = data['settings'] if data else api_settings(authority, ref)
    content = payload['text']
    if data and isinstance(content, list):
        from gateway.session_api_media import restore_api_images
        content = restore_api_images(content, data.get('media') or [])
    return {'adapter': adapter, 'settings': settings, 'history': data['history'] if data else None,
            'content': content, 'turn_author': data.get('turn_author') if data else None,
            'relay_metadata': data.get('relay_metadata') if data else None,
            'resume_unanswered': bool(data and data.get('resume_unanswered'))}


def _api_observers(authority, session_id):
    execution = authority.sessions[session_id].event_stream.execution
    admission_id = execution.get('admission_id') if execution else None
    return tuple(getattr(authority, 'api_observers', {}).get(admission_id, ()))


def _notify_observers(authority, session_id, key, *args, **kwargs):
    """Observer callbacks are request-owned sinks; one that raises (closed socket, torn-down
    loop) must not abort canonical execution or starve the other observers. Returns whether
    any observer accepted the event."""
    import logging
    accepted = False
    for observer in _api_observers(authority, session_id):
        callback = observer.get(key)
        if callback:
            try:
                callback(*args, **kwargs)
                accepted = True
            except Exception:
                logging.getLogger(__name__).warning('API observer %s failed for %s', key, session_id, exc_info=True)
    return accepted


def wire_api_observers(agent, owner, want_interim):
    """Hand this admission's API observers (Chat/Responses SSE, runs) the agent's reasoning,
    status, commentary and tool progress callbacks, the ones the direct API path gave the agent
    itself. Each event is fenced to the exact running claim, so a settled worker's late callback is inert;
    the turn's own status/commentary sinks keep running after the observers."""
    authority, session_id, generation = owner

    def publish(key, *args, **kwargs):
        with authority.sessions[session_id].event_stream.lock:
            try:
                authority.check_approval_generation(session_id, generation)
            except RuntimeStoreError:
                return False
            return _notify_observers(authority, session_id, key, *args, **kwargs)

    status, interim = agent.status_callback, agent.interim_assistant_callback

    def status_callback(kind, message=None):
        publish('status_callback', kind, message)
        if status is not None:
            status(kind, message)

    def interim_assistant_callback(text, *, already_streamed=False):
        publish('interim_assistant_callback', text, already_streamed=already_streamed)
        if interim is not None:
            interim(text, already_streamed=already_streamed)
    progress = agent.tool_progress_callback

    def tool_progress_callback(event_type, *args, **kwargs):
        if event_type in ('tool.started', 'tool.completed'):
            publish_api_tool_progress(authority, session_id, generation, event_type, *args, **kwargs)
        if progress is not None:
            return progress(event_type, *args, **kwargs)
    agent.reasoning_callback = lambda text: publish('reasoning_callback', text)
    agent.status_callback = status_callback
    agent.tool_progress_callback = tool_progress_callback
    if want_interim:
        agent.interim_assistant_callback = interim_assistant_callback


def publish_api_event(authority, session_id, event_type, payload):
    if event_type == 'message.delta' and payload['text'] is not None:  # a None segment boundary is viewer-only
        return _notify_observers(authority, session_id, 'stream_delta_callback', payload['text'])
    return False


def publish_api_tool_progress(authority, session_id, generation, *args, **kwargs):
    """A ``tool_progress_callback`` tool event as its producer emitted it, for the admission's
    progress-only observers; never inferred again from formatted or bounded result text."""
    with authority.sessions[session_id].event_stream.lock:
        try:
            authority.check_approval_generation(session_id, generation)
        except RuntimeStoreError:
            return
        _notify_observers(authority, session_id, 'tool_progress_callback', *args, **kwargs)


def publish_api_tool_event(authority, session_id, generation, event_type, call_id, tool_name, args, result=None):
    """Real tool arguments/results for the admission's API observers (Responses streaming,
    runs SSE); never part of the shared viewer event stream."""
    live = authority.sessions[session_id]
    with live.event_stream.lock:
        try:
            authority.check_approval_generation(session_id, generation)
        except RuntimeStoreError:
            return
        if event_type == 'tool.start':
            _notify_observers(authority, session_id, 'tool_start_callback', call_id, tool_name, args or {})
        elif event_type == 'tool.complete':
            _notify_observers(authority, session_id, 'tool_complete_callback', call_id, tool_name, args or {}, result)


def api_runtime_locked():
    """The executing API admission's settings when they carry a confirmed Browser model lock, else
    None. Main's ``_create_agent`` passes ``fallback_model=None`` under the lock and refuses a
    settled turn whose served runtime differs from it (``_turn_runtime_metadata``)."""
    current = api_execution.get()
    return current if current and current['settings'].get('confirmed_runtime_lock') else None


def prepare_api_runtime(model, runtime_kwargs):
    current = api_execution.get()
    if current is None:
        return model, runtime_kwargs
    options = current['settings']
    route = options.get('route')
    configured = current['adapter']._model_routes.get(options.get('requested_model'))
    if configured and {k: v for k, v in configured.items() if k != 'api_key'} == route:
        route = configured
    model, _, _, _ = current['adapter']._select_agent_runtime(runtime_kwargs, model,
        requested_model=options.get('requested_model'), requested_provider=options.get('requested_provider'),
        route=route, session_model=options.get('session_model'),
        confirmed_runtime_lock=bool(options.get('confirmed_runtime_lock')),
        gateway_session_key=None, session_id=None)
    return model, runtime_kwargs


def _trusted_runner(adapter):
    runner = getattr(adapter, 'gateway_runner', None)
    if runner is None:
        return None
    if getattr(runner, 'session_authority', None) is None and getattr(runner, 'session_authorities', None) is None:
        return None
    return runner


def merge_reasoning_effort(options, effort):
    """Put one committed tier on the snapshot the agent will actually build.

    Does not replace model, provider, or the rest of a browser lock.
    """
    if not isinstance(effort, str) or not effort:
        return options if isinstance(options, dict) else {}
    merged = dict(options) if isinstance(options, dict) else {}
    merged['reasoning_effort'] = effort
    reasoning = merged.get('reasoning')
    if isinstance(reasoning, dict):
        reasoning = dict(reasoning)
        reasoning['effort'] = effort
        merged['reasoning'] = reasoning
    return merged


def commit_pending_if_any(authority, session_id):
    """Commit a pending pin on the request row, then the lineage root if that is a different row.

    No pending is not a write. A refusal from the service is raised, never turned into success.
    """
    from clawd_server.effort_service import commit_deferred_effort, describe_session_effort

    logical = authority.logical_owner(session_id) or session_id
    order = [session_id] if session_id == logical else [session_id, logical]
    primary = None
    for sid in order:
        if not sid:
            continue
        session = authority.db.get_session(sid)
        if not isinstance(session, dict):
            continue
        described = describe_session_effort(session, running=None)
        if described.get('source') != 'pending':
            continue
        view = commit_deferred_effort(
            authority.db, sid, authority=authority, admission_held=True)
        if sid == session_id or primary is None:
            primary = view
    return primary


@asynccontextmanager
async def profile_admission_lock(adapter, session_id):
    """Hold this profile's admission lock around the commit and the tier snapshot.

    ``None`` only when this process has no session authority to hold. A mismatched
    store raises; callers must not continue as if the pin was committed.
    """
    runner = _trusted_runner(adapter)
    if runner is None:
        yield None
        return
    from gateway.session_authorities import require_active_authority
    from gateway.session_authority import keyed_turn

    authority = require_active_authority(runner, adapter._ensure_session_db)
    if authority.db is not adapter._ensure_session_db():
        from hermes_state_runtime import RuntimeStoreError
        raise RuntimeStoreError('profile_mismatch')
    logical = authority.logical_owner(session_id) or session_id
    live = authority.sessions.get(logical)
    async with keyed_turn(authority._route_turns, 'clawd-admission:' + logical):
        if live is None:
            yield authority, session_id
        else:
            async with live.mutation_lock:
                yield authority, session_id


def apply_committed_effort(mapping, effort):
    if not isinstance(mapping, dict) or not isinstance(effort, str) or not effort:
        return
    mapping['model_options'] = merge_reasoning_effort(mapping.get('model_options'), effort)


async def commit_trusted_pending_effort(adapter, session_id, *snapshots):
    """Commit under the held admission lock, then write the tier into snapshots before release.

    Returns the view, or None when this process has no authority or nothing pending.
    ``EffortServiceError`` and ``RuntimeStoreError`` propagate. Neither is success.
    """
    async with profile_admission_lock(adapter, session_id) as held:
        if held is None:
            return None
        authority, sid = held
        view = await asyncio.to_thread(commit_pending_if_any, authority, sid)
        if isinstance(view, dict) and view.get('persisted') is True:
            effort = view.get('effort')
            for snapshot in snapshots:
                apply_committed_effort(snapshot, effort)
        return view


async def prepare_admitted_session_chat(adapter, request):
    """Commit a pending tier before ``_prepare_session_chat`` copies the lock into this request."""
    from clawd_server.effort_service import EffortServiceError
    from gateway.platforms.api_server import _error_response

    session_id = request.match_info['session_id']
    try:
        async with profile_admission_lock(adapter, session_id) as held:
            view = None
            if held is not None:
                view = await asyncio.to_thread(commit_pending_if_any, held[0], held[1])
            # The replace inside ``_prepare_session_chat`` must see this tier.
            # Only the row that was actually committed qualifies. A lineage-root
            # view must not be written onto a different request row.
            effort = None
            if (isinstance(view, dict) and view.get('persisted') is True
                    and view.get('session_id') == session_id
                    and isinstance(view.get('effort'), str)):
                effort = view['effort']
            token = committed_effort_for_lock.set(effort)
            try:
                ctx, err = await adapter._prepare_session_chat(request)
            finally:
                committed_effort_for_lock.reset(token)
            if err is None and isinstance(view, dict) and view.get('persisted') is True:
                apply_committed_effort(ctx.get('run_kwargs'), view.get('effort'))
                apply_committed_effort(ctx.get('runtime_request'), view.get('effort'))
            return ctx, err
    except EffortServiceError as exc:
        return None, _error_response(exc.message, exc.status, code=exc.code)
    except RuntimeStoreError as exc:
        code = 'effort_admission_required' if exc.reason in ('profile_mismatch', 'runtime_draining') else 'effort_run_state_unavailable'
        status = 409 if code == 'effort_admission_required' else 503
        return None, _error_response('档位未能在该 profile 的准入锁内提交，当前请求未改用新档', status, code=code)
