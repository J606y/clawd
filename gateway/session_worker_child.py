"""Owner-reserved delegated children of a verified worker execution (#141793).

A worker never mints a child. It asks on its OWN fenced receipt stream (epoch, assignment,
generation, sequence), and the owner creates the child's session row and execution, binds it to
the parent's verified process identity and the worker's fresh secret, and enforces the frozen
policy's delegation caps. The child then persists through the ordinary ``worker.persist`` path
under its own claim. The owner's state.db parent is the only project profile: before this
receipt commits, the parent's current project row is snapshotted. That projects.db write is
not a cross-database transaction. Inheritance failure is a store error, not a refusal receipt,
so no child id is published. Expected policy refusals (caps, draining) remain receipts, not
errors: an error would leave the parent's journal pending and refuse every later write of its turn.
"""
import secrets
from functools import partial

from hermes_state_runtime import RuntimeStoreError, apply_worker_receipt
from hermes_state_runtime_workers import CHILD_SEPARATOR, descendant_range

_DEFAULTS = {'max_concurrent_children': 10, 'max_spawn_depth': 1}


def _limit(section, key):
    try:
        return max(1, int(section.get(key, _DEFAULTS[key])))
    except (TypeError, ValueError):
        return _DEFAULTS[key]


def delegation_limits(authority, execution_id, session_id):
    """``(max_concurrent_children, max_spawn_depth)`` from the root conversation's frozen policy
    (the profile's config for a session with no local policy), never from the asking worker."""
    from gateway.session_policy import policy_for_source, profile_config
    root = execution_id.split(CHILD_SEPARATOR, 1)[0]
    if root != execution_id:
        with authority.db._read_ctx() as conn:
            row = conn.execute('SELECT session_id FROM worker_executions WHERE execution_id=?', (root,)).fetchone()
        session_id = row[0] if row else session_id
    live = authority.sessions.get(authority.logical_owner(session_id))
    policy = policy_for_source(authority.runner, live.source) if live is not None else None
    config = policy.config(authority) if policy is not None else profile_config(authority)
    section = config.get('delegation') or {}
    return _limit(section, 'max_concurrent_children'), _limit(section, 'max_spawn_depth')


def _inheritance_refused(exc):
    """Store error, not a capacity refusal. A refusal receipt would publish the turn as normal."""
    from hermes_state_runtime import RuntimeStoreError
    raise RuntimeStoreError(
        'project_inheritance_failed',
        field='project',
        detail='parent project was not snapshotted; child not published',
    ) from exc


def _reservation_state_db(db):
    """The profile file this owner connection is writing. Missing identity does not publish."""
    from clawd_projects.inheritance import ProjectInheritanceError, trusted_state_db_home
    path = getattr(db, 'db_path', None)
    try:
        if path is None:
            raise ProjectInheritanceError(
                'worker reservation has no state.db path; refusing to publish a child'
            )
        trusted_state_db_home(path)
    except ProjectInheritanceError as exc:
        _inheritance_refused(exc)
    return path


def _snapshot_reserved_child_project(state_path, parent_sid, child_sid):
    """Copy the parent's current project before this reservation's state commit.

    Does not walk ancestors and does not use process home. An existing child
    project row is kept. A token written by this call is conditionally released
    only if a later check in this call fails; other session project rows are
    not deleted. The projects.db commit is not atomic with the state commit.
    """
    from clawd_projects.inheritance import (
        ProjectInheritanceError,
        copy_parent_project_before_release,
        release_owned_child_binding,
    )
    proof = None
    try:
        proof = copy_parent_project_before_release(
            parent_sid, child_sid, state_db_path=state_path,
        )
        if proof.wrote and not proof.write_token:
            raise ProjectInheritanceError(
                'snapshot wrote a binding without a write_token from this call'
            )
    except ProjectInheritanceError as exc:
        if proof is not None and proof.wrote and proof.write_token and proof.project_id:
            try:
                release_owned_child_binding(
                    child_sid,
                    state_db_path=state_path,
                    project_id=proof.project_id,
                    write_token=proof.write_token,
                )
            except Exception as release_exc:
                _inheritance_refused(release_exc)
        _inheritance_refused(exc)
    return proof


def reserve_child_execution(db, conn, session_id, payload, *, epoch, execution_id, identity, principal, limits, refused):
    from hermes_state_ids import new_session_id
    from hermes_state_keys import admission_fingerprint
    from hermes_state_runtime import _secret_digest
    from hermes_state_worker_lifecycle import insert_session_row_in_transaction
    secret = payload.get('secret') if isinstance(payload, dict) else None
    if set(payload) != {'secret'} or not isinstance(secret, str) or not 16 <= len(secret) <= 256:
        raise RuntimeStoreError('invalid_params')
    max_children, max_depth = limits
    prefix = execution_id + CHILD_SEPARATOR
    live = conn.execute("SELECT COUNT(*) FROM worker_executions WHERE execution_id>=? AND execution_id<? "
                        "AND instr(substr(execution_id,?),'/')=0 AND status!='terminal'",
                        (*descendant_range(execution_id), len(prefix) + 1)).fetchone()[0]
    if refused is None and prefix.count(CHILD_SEPARATOR) > max_depth:
        refused = 'delegation_depth_exceeded'
    if refused is None and live >= max_children:
        refused = 'delegation_capacity'
    if refused is not None:
        return {'refused': refused}
    # Identity is this connection's state.db, not the worker payload, process home, or an ancestor.
    # Checked before the child row so a missing profile does not depend on a later guess.
    state_path = _reservation_state_db(db)
    parent = conn.execute('SELECT profile_name FROM sessions WHERE id=?', (session_id,)).fetchone()
    if parent is None:
        from clawd_projects.inheritance import ProjectInheritanceError
        _inheritance_refused(ProjectInheritanceError(
            'parent session is not in this state.db; refusing to publish a child'
        ))
    child_sid, child_eid = new_session_id(), prefix + secrets.token_hex(8)
    insert_session_row_in_transaction(db, conn, child_sid, 'subagent', model_config={'_delegate_from': session_id},
                                      parent_session_id=session_id, profile_name=parent['profile_name'])
    scope = dict(identity, session_id=child_sid, execution_id=child_eid, generation=0, secret=secret)
    claim = admission_fingerprint(canonical_target=child_sid, payload=scope | {'principal': principal})
    conn.execute("INSERT INTO worker_executions(execution_id,session_id,kind,owner_epoch,generation,status,adoption_digest) "
                 "VALUES(?,?,'child',?,0,'running',?)", (child_eid, child_sid, epoch, _secret_digest(claim)))
    # projects.db commits on its own, before this state transaction commits. Not one atomic commit.
    # Failure raises, so this receipt is not stored and no child id is returned.
    _snapshot_reserved_child_project(state_path, session_id, child_sid)
    return {'child': {'session_id': child_sid, 'execution_id': child_eid, 'generation': 0}}


async def worker_child_request(connection, ref, params):
    """worker.persist ``child.reserve``: the parent's full claim and assignment are verified first."""
    import asyncio
    from gateway.session_worker import _SCOPE, _claim, _verify
    if set(params) != _SCOPE | {'epoch', 'sequence', 'operation', 'payload'}:
        raise RuntimeStoreError('invalid_params')
    claim = _claim(connection, ref, params)
    if 'worker:adopt' not in connection.actor.capabilities:
        raise RuntimeStoreError('permission_denied')
    _verify(connection, ref, params, claim)
    authority = connection.authority

    def reserve():
        refused, limits = None, (0, 0)
        try:
            authority._require_admission_open()
            limits = delegation_limits(authority, params['execution_id'], params['session_id'])
        except RuntimeStoreError as exc:
            refused = exc.reason
        handler = partial(reserve_child_execution, epoch=params['epoch'], execution_id=params['execution_id'],
                          identity={key: params[key] for key in ('profile_id', 'pid', 'birth')},
                          principal=connection.actor.subject, limits=limits, refused=refused)
        return apply_worker_receipt(authority.db, **{key: params[key] for key in (
            'epoch', 'execution_id', 'session_id', 'generation', 'sequence', 'operation', 'payload')},
            handlers={'child.reserve': handler})
    return await asyncio.to_thread(reserve)
