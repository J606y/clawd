"""A delegated child's store inside a worker: owner-reserved, never self-registered (#141793).

The parent's ``RuntimeSessionStore`` asks the owner for a child on its own receipt stream; the
owner mints the child's session row and execution (gateway/session_worker_child.py) and snapshots
the parent's current project before that state transaction commits. This module does not inherit,
does not bind a project, and does not read process home or ancestors. A refusal (capacity, depth,
draining) is a ValueError so ``delegate_task`` reports it as a tool error. A reservation that
does not return a child session id is not published.
"""
import secrets
from functools import partial

from agent.runtime_session_store import RuntimeSessionStore, call_off_event_loop

_REFUSALS = {
    'delegation_capacity': 'the gateway owner refused another subagent: this execution already runs '
                           'delegation.max_concurrent_children of them',
    'delegation_depth_exceeded': 'the gateway owner refused a subagent this deep (delegation.max_spawn_depth)',
}


class ChildSessionStore(RuntimeSessionStore):
    binds_delegation_ledger = False

    @property
    def reserved_session_id(self):
        return self.scope['session_id']

    def create_session(self, session_id, source, **fields):
        # The owner created this row at reservation with its source, lineage and profile; only
        # the constructor facts are the child's to add.
        from hermes_state_worker_lifecycle import _CONSTRUCTOR_CONFIG
        config = {k: v for k, v in (fields.get('model_config') or {}).items() if k in _CONSTRUCTOR_CONFIG}
        return super().create_session(session_id, 'subagent', model=fields.get('model'),
                                      model_config=config or None, system_prompt=fields.get('system_prompt'))

    def close(self):
        # The child's agent closes once, after its last write: that is this execution's end.
        try:
            if not self._outbox_owner.closed and not self.failure:
                self.finish()
        finally:
            super().close()


def reserve_child_store(parent):
    """A ``ChildSessionStore`` the owner reserved under ``parent``'s verified execution.

    Project identity is already snapshotted by the owner, or the owner raised and
    this function does not run. Do not bind here, and do not treat a project field
    on the receipt as a binding instruction.
    """
    secret = secrets.token_urlsafe(32)
    # Reached from delegate_task's tool callback, which can run on Relay's loop (#139029).
    result = call_off_event_loop(parent._apply, 'child.reserve', {'secret': secret})
    if not isinstance(result, dict) or 'refused' in result:
        refused = result.get('refused') if isinstance(result, dict) else None
        raise ValueError(_REFUSALS.get(refused, 'the gateway owner refused a subagent: ' + str(refused or 'no child session')))
    child = result.get('child')
    if not isinstance(child, dict) or not isinstance(child.get('session_id'), str) or not child['session_id']:
        raise RuntimeError('owner reservation did not return a child session; refusing to publish')
    if 'execution_id' not in child or 'generation' not in child:
        raise RuntimeError('owner reservation omitted execution identity; refusing to publish')
    scope = dict(
        parent.scope,
        session_id=child['session_id'],
        execution_id=child['execution_id'],
        generation=child['generation'],
        secret=secret,
    )
    outbox = parent.path.parent / ('child-' + child['execution_id'].rsplit(':', 1)[1])
    # Every child RPC hops off a running loop, not only this reservation: the child is built, run
    # and closed inside delegate_task's callback, and its constructor already reads the store
    # (ContextCompressor.bind_session_state), which WorkerRPC refuses on Relay's loop.
    return ChildSessionStore(partial(call_off_event_loop, parent.rpc), scope, outbox)
