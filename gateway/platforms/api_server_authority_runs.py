"""API run controls resolve durable claims, never adapter agent/task ownership."""
from dataclasses import asdict

from gateway.session_contract import Principal, SessionRef
from gateway.session_results import admission_result
from hermes_state_runtime import RuntimeStoreError, _row


def _authority(adapter):
    """The routed profile's authority: ``/p/<profile>/`` middleware already entered its scope."""
    from gateway.session_authorities import active_authority
    return active_authority(adapter.gateway_runner)


# Malformed input is 400 and a retryable refusal 503; every other reason (ownership, generation,
# identity) keeps the documented 409, as /v1/runs clients already branch on it. ``chain_moved``: a
# chain's lookup went stale before its bind (``bind_api_steps``); a retry looks it up again.
_STORE_ERROR_STATUS = {'invalid_params': 400, 'runtime_draining': 503, 'storage_unavailable': 503,
                       'chain_moved': 503}


def store_error_response(_openai_error, exc):
    """A refused store call -> HTTP: a ``RuntimeStoreError`` keeps its reason; any other failure
    (SQLite writer patience exhausted, I/O) is ``storage_unavailable``."""
    from aiohttp import web
    reason = exc.reason if isinstance(exc, RuntimeStoreError) else 'storage_unavailable'
    if reason == 'admission_queued':
        # Accepted and still queued for the next owner, so not retryable: no Retry-After, and
        # x-should-retry tells the OpenAI/Anthropic SDKs not to retry the 503 on their own.
        return web.json_response(_openai_error(str(exc), err_type='server_error', code=reason), status=503,
                                 headers={'x-should-retry': 'false'})
    status = _STORE_ERROR_STATUS.get(reason, 409)
    return web.json_response(_openai_error(reason, code=reason), status=status,
                             headers={'Retry-After': '1'} if status == 503 else None)


async def continued_session(adapter, session_id, request_id=None):
    """``continued_api_session`` for an HTTP door, before its response starts -> ``(session_id,
    None)`` or ``(session_id, error response)``: a busy or unreadable store is the retryable 503
    ``storage_unavailable`` (``store_error_response``), as for the header-less chat lookup."""
    import asyncio
    import sqlite3
    from gateway.platforms.api_server import _openai_error
    from gateway.session_api import continued_api_session
    try:
        return await asyncio.to_thread(
            continued_api_session, adapter.gateway_runner, session_id, request_id), None
    except (RuntimeStoreError, sqlite3.Error, OSError) as exc:
        return session_id, store_error_response(_openai_error, exc)


def run_admission(adapter, run_id):
    authority = _authority(adapter)
    if authority is None:
        return None
    # A streaming completion's public run id names its admission through the in-memory binding
    # its own request holds; every other run id is the admission's durable request id.
    alias = getattr(adapter, '_run_admission_aliases', {}).get(run_id)
    column, value = ('admission_id', alias) if alias else ('request_id', run_id)
    with authority.db._read_ctx() as conn:
        rows = conn.execute(f"SELECT * FROM session_admissions WHERE principal_id='api' AND {column}=?",
                            (value,)).fetchall()
    if len(rows) > 1:
        raise RuntimeStoreError('admission_conflict')
    return (authority, _row(rows[0])) if rows else None


def run_projection(adapter, run_id):
    owned = run_admission(adapter, run_id)
    if owned is None:
        return None
    authority, row = owned
    # ``unknown`` is not terminal: later turns wait for POST /v1/runs/{id}/resolve-unknown, and
    # ``interrupted`` is in TERMINAL_STATUSES, so a poller would stop at a paused head.
    status = {'queued': 'queued', 'started': 'running', 'unknown': 'unknown', 'terminal': row['outcome']}.get(row['status'])
    saved = admission_result(authority.db, row['admission_id'])
    result = saved.get('result', {}) if saved else {}
    if row['status'] == 'terminal':
        if result.get('interrupted') or row['outcome'] == 'interrupted':
            status = 'cancelled'
        elif result.get('failed') or result.get('error'):
            status = 'failed'
        elif row['outcome'] == 'completed' and saved:
            # The run SSE's own mapper: a `partial` or `completed: false` turn (iteration
            # budget, truncation) settles with outcome `completed` but is a failed run there,
            # so polling must not report `completed` for the same receipt.
            from gateway.platforms.api_server_runs import terminal_run_status
            status = terminal_run_status(result)[0]
    pending = []
    live = authority.sessions.get(row['target_session_id'])
    if live is not None and row['status'] == 'started':
        pending = list(live.controls.snapshot(row['target_session_id'], row['generation']))
        if any(prompt.get('kind') == 'approval' for prompt in pending):
            status = 'waiting_for_approval'  # the documented run state main's run store reports
    return {'pending_controls': pending, 'run_id': run_id, 'status': status, 'session_id': row['target_session_id'],
            'admission_id': row['admission_id'], 'execution_generation': row['generation'],
            'output': result.get('final_response', ''), 'usage': saved.get('usage', {}) if saved else {}}


async def send_clarify(adapter, *, chat_id, **kwargs):
    from gateway.platforms.base import SendResult
    authority = _authority(adapter)
    if authority is None or chat_id not in authority.sessions:
        return SendResult(success=False, error='No canonical API session')
    handle = authority._handle(SessionRef(authority.profile_id, chat_id))
    if handle.execution_state != 'running':
        return SendResult(success=False, error='No active API execution')
    # TurnRunner registers the shared prompt after this ACK; HTTP polling and WS
    # subscribers consume that projection rather than an adapter-local message.
    return SendResult(success=True, message_id=kwargs['clarify_id'])


async def respond_run(adapter, run_id, body, *, kind):
    import uuid
    owned = run_admission(adapter, run_id)
    if owned is None:
        raise RuntimeStoreError('not_found')
    authority, row = owned
    generation = body.get('execution_generation')
    prompt_id = body.get('request_id')
    field = 'choice' if kind == 'approval' else 'answer'
    if set(body) != {'request_id', 'execution_generation', field}:
        raise RuntimeStoreError('invalid_params')
    if row['status'] != 'started' or type(generation) is not int or generation != row['generation']:
        raise RuntimeStoreError('stale_generation')
    ref = SessionRef(authority.profile_id, row['target_session_id'])
    actor = Principal('api', authority.profile_id,
        frozenset({'session:read', 'session:approve', 'session:respond'}), 'api-control:' + uuid.uuid4().hex)
    snapshot = await authority.attach(actor, ref, history=False)  # prompts only
    try:
        if not any(p['prompt_id'] == prompt_id and p['kind'] == kind for p in snapshot.prompts):
            raise RuntimeStoreError('approval_not_pending')
        return await authority.respond(actor, ref, generation, prompt_id, {field: body[field]}, kind=kind)
    finally:
        await authority.detach(actor, snapshot.subscription_id)


async def stop_run(adapter, run_id):
    owned = run_admission(adapter, run_id)
    if owned is None:
        raise RuntimeStoreError('not_found')
    authority, row = owned
    admission_id = row['admission_id']
    ref = SessionRef(authority.profile_id, row['target_session_id'])
    actor = Principal('api', authority.profile_id,
                      frozenset({'session:submit', 'session:control'}), 'api-run:' + run_id)
    if row['status'] == 'queued':
        try:
            await authority.cancel_queued(actor, ref, admission_id)
        except RuntimeStoreError as exc:
            # The drain claimed the head between the read above and the cancel commit: the run is
            # executing now, so Stop that exact claim below instead of refusing a live run.
            if exc.reason != 'stale_generation':
                raise
            refreshed = run_admission(adapter, run_id)
            # Gone. The row read above is not a receipt and is not a generation to interrupt.
            if refreshed is None:
                raise RuntimeStoreError('not_found')
            authority, row = refreshed
            if (row.get('admission_id') != admission_id
                    or row.get('target_session_id') != ref.session_id
                    or getattr(authority, 'profile_id', None) != ref.profile_id):
                raise RuntimeStoreError('admission_conflict')
            if row.get('status') == 'queued':
                raise RuntimeStoreError('stale_generation')
            if row.get('status') == 'unknown':
                raise RuntimeStoreError('unknown_execution')
        else:
            adapter._stopping_run_ids.add(run_id)
            waiter = authority.waiters.pop(admission_id, None)
            if waiter is not None and not waiter.done():
                waiter.set_result(None)
            return run_projection(adapter, run_id)
    if row['status'] == 'started':
        generation = row.get('generation')
        owner_epoch = row.get('owner_epoch')
        # A restarted authority can reuse a generation number. Do not interrupt that epoch.
        if (type(generation) is not int or type(owner_epoch) is not int
                or owner_epoch != getattr(authority, 'epoch', None)):
            raise RuntimeStoreError('stale_generation')
        await authority.interrupt(actor, ref, generation)
        refreshed = run_admission(adapter, run_id)
        if refreshed is None:
            raise RuntimeStoreError('not_found')
        _authority, row = refreshed
        if (row.get('admission_id') != admission_id
                or row.get('target_session_id') != ref.session_id
                or getattr(_authority, 'profile_id', None) != ref.profile_id
                or row.get('generation') != generation
                or row.get('owner_epoch') != owner_epoch):
            raise RuntimeStoreError('stale_generation')
        if row.get('status') == 'unknown':
            raise RuntimeStoreError('unknown_execution')
        if row.get('status') == 'terminal':
            return run_projection(adapter, run_id)
        adapter._stopping_run_ids.add(run_id)
        return {'run_id': run_id, 'status': 'stopping', 'admission_id': admission_id}
    elif row['status'] == 'unknown':
        raise RuntimeStoreError('unknown_execution')
    return run_projection(adapter, run_id)


def read_exact_admission(admitted):
    """Store row for this admission.

    A missing row is ``not_found``. The caller's snapshot is never returned, so a
    deleted admission cannot be reported as terminal from memory.
    """
    authority, ref, row = admitted
    admission_id = row.get('admission_id') if isinstance(row, dict) else None
    if not isinstance(row, dict) or not admission_id:
        raise RuntimeStoreError('admission_conflict')
    if getattr(ref, 'profile_id', None) != getattr(authority, 'profile_id', None):
        raise RuntimeStoreError('profile_mismatch')
    if getattr(ref, 'session_id', None) != row.get('target_session_id'):
        raise RuntimeStoreError('admission_conflict')
    from hermes_state_runtime import get_session_admission
    current = get_session_admission(authority.db, admission_id=admission_id)
    if not isinstance(current, dict):
        raise RuntimeStoreError('not_found')
    if (current.get('admission_id') != admission_id
            or current.get('request_id') != row.get('request_id')
            or current.get('target_session_id') != row.get('target_session_id')
            or current.get('principal_id') != 'api'
            or row.get('principal_id') not in (None, 'api')):
        raise RuntimeStoreError('admission_conflict')
    return current


def _same_started_claim(current, *, admission_id, session_id, generation, owner_epoch):
    return (current.get('admission_id') == admission_id
            and current.get('target_session_id') == session_id
            and current.get('generation') == generation
            and current.get('owner_epoch') == owner_epoch)


async def _interrupt_exact_generation(adapter, run_id, admitted, actor):
    """Interrupt this admission's generation. A newer epoch is ``stale_generation``."""
    authority, ref, _row = admitted
    current = read_exact_admission(admitted)
    if current.get('status') == 'unknown':
        raise RuntimeStoreError('unknown_execution')
    if current.get('status') == 'terminal':
        return 'cancelled' if current.get('outcome') == 'cancelled' else 'settled'
    if current.get('status') != 'started':
        raise RuntimeStoreError('admission_conflict')
    generation = current.get('generation')
    owner_epoch = current.get('owner_epoch')
    if (type(generation) is not int or type(owner_epoch) is not int
            or owner_epoch != getattr(authority, 'epoch', None)):
        raise RuntimeStoreError('stale_generation')
    await authority.interrupt(actor, ref, generation)
    after = read_exact_admission(admitted)
    if after.get('status') == 'unknown':
        raise RuntimeStoreError('unknown_execution')
    if after.get('status') == 'terminal':
        return 'cancelled' if after.get('outcome') == 'cancelled' else 'settled'
    if not _same_started_claim(
            after, admission_id=current['admission_id'], session_id=ref.session_id,
            generation=generation, owner_epoch=owner_epoch):
        raise RuntimeStoreError('stale_generation')
    stopping = getattr(adapter, '_stopping_run_ids', None)
    if stopping is not None:
        stopping.add(run_id)
    return 'stopping'


async def _classify_exact_row(adapter, run_id, admitted, actor, *, retry_queued=False):
    authority, ref, _row = admitted
    current = read_exact_admission(admitted)
    if current.get('status') == 'unknown':
        raise RuntimeStoreError('unknown_execution')
    if current.get('status') == 'terminal':
        return 'cancelled' if current.get('outcome') == 'cancelled' else 'settled'
    if current.get('status') == 'started':
        return await _interrupt_exact_generation(adapter, run_id, admitted, actor)
    if current.get('status') == 'queued' and retry_queued:
        await authority.cancel_queued(actor, ref, current['admission_id'])
        return await _classify_exact_row(adapter, run_id, admitted, actor, retry_queued=False)
    raise RuntimeStoreError('stale_generation')


async def cancel_exact_admission(adapter, run_id, admitted):
    """Cancel or interrupt only the admission this call just committed.

    Confirms the same profile, request, session and admission. A started row is
    interrupted only at its own generation and owner epoch; a newer epoch raises
    ``stale_generation`` and is not interrupted. A missing row is ``not_found``.
    The caller's snapshot is never a receipt. Identity failures raise; they are
    not a ``mismatch`` success. Only ``cancelled``, ``stopping`` and ``settled``
    mean the stop was applied or the row had already ended.
    """
    authority, ref, row = admitted
    if not run_id or not isinstance(row, dict) or row.get('request_id') != run_id:
        raise RuntimeStoreError('admission_conflict')
    if getattr(ref, 'profile_id', None) != getattr(authority, 'profile_id', None):
        raise RuntimeStoreError('profile_mismatch')
    current = read_exact_admission(admitted)
    if current.get('request_id') != run_id:
        raise RuntimeStoreError('admission_conflict')
    if current.get('status') == 'unknown':
        raise RuntimeStoreError('unknown_execution')
    if current.get('status') == 'terminal':
        return 'cancelled' if current.get('outcome') == 'cancelled' else 'settled'
    actor = Principal(
        'api', authority.profile_id,
        frozenset({'session:submit', 'session:control'}), 'api-run:' + run_id)
    if current.get('status') == 'queued':
        try:
            await authority.cancel_queued(actor, ref, current['admission_id'])
        except RuntimeStoreError as exc:
            if exc.reason == 'unknown_execution':
                raise
            if exc.reason != 'stale_generation':
                raise
            # Claimed between the read and the commit. Stop that claim only when
            # it is still this admission; do not interrupt a newer epoch.
            return await _classify_exact_row(
                adapter, run_id, admitted, actor, retry_queued=True)
        return await _classify_exact_row(adapter, run_id, admitted, actor)
    if current.get('status') != 'started':
        raise RuntimeStoreError('admission_conflict')
    return await _interrupt_exact_generation(adapter, run_id, admitted, actor)


async def resolve_unknown_run(adapter, run_id, body):
    """Resolve only the exact unknown admission durably bound to an owned API run."""
    owned = run_admission(adapter, run_id)
    if owned is None:
        raise RuntimeStoreError('not_found')
    authority, row = owned
    if not isinstance(body, dict) or set(body) != {'admission_id', 'execution_generation'}:
        raise RuntimeStoreError('invalid_params')
    if body['admission_id'] != row['admission_id']:
        raise RuntimeStoreError('not_found')
    generation = body['execution_generation']
    # Status/generation are the store's decision (``resolve_unknown_session_input``): it also replays the
    # committed receipt to an exact retry whose acknowledgement was lost, which a pre-check here refused.
    actor = Principal(
        'api', authority.profile_id, frozenset({'session:submit', 'session:control'}),
        'api-run:' + run_id)
    receipt = await authority.resolve_unknown(
        actor, SessionRef(authority.profile_id, row['target_session_id']),
        row['admission_id'], generation)
    return asdict(receipt)
