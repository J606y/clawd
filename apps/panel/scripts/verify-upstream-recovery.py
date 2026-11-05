"""Validate the public-source patch in isolation; never import installed Hermes.

Usage: python3 scripts/verify-upstream-recovery.py --source-root PUBLIC_SOURCE_DIR
Payloads are synthetic. Run against pristine 8edc5ea255aa to see RED, then the
same files with patches/hermes-agent-8edc5ea2-recovery.patch applied for GREEN.
"""
import argparse
import ast
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root', required=True, type=Path)
args = parser.parse_args()
ROOT = args.source_root.resolve()


def source(path):
    return ast.parse((ROOT / path).read_text())


def definitions(tree, names):
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(selected) == len(names), (names, len(selected))
    return selected


def load(nodes, ns):
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[])), 'public_source_seam.py', 'exec'), ns)


class RecoveryTests(unittest.TestCase):
    def test_api_trust_stamp_does_not_turn_ordinary_input_into_a_notification(self):
        ns = {'ContextVar': ContextVar, 'contextmanager': contextmanager, 'Any': object,
              'RuntimeStoreError': type('RuntimeStoreError', (Exception,), {})}
        display = source('gateway/session_display.py')
        load([n for n in display.body if isinstance(n, (ast.Assign, ast.AnnAssign, ast.FunctionDef))], ns)
        class StripGatewayImports(ast.NodeTransformer):
            def visit_ImportFrom(self, node):
                return None if node.module and node.module.startswith('gateway.') else node
        filters = source('gateway/response_filters.py')
        names = {'display_kind_for_event', 'display_metadata_for_event', '_producer_card'}
        constants = {'INTERNAL_NOTIFICATION_DISPLAY_KIND', 'PRODUCER_NOTICE_DISPLAY_KINDS'}
        selected = [n for n in filters.body if (isinstance(n, ast.FunctionDef) and n.name in names)
                    or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in n.targets))]
        load([StripGatewayImports().visit(n) for n in selected], ns)
        ingress = source('gateway/session_ingress.py')
        stamps = [n for n in ast.walk(ingress) if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == 'event'
                          and t.attr in ('internal', '_api_admitted_user_input') for t in n.targets)]
        self.assertTrue(stamps)
        def stamped(internal=False, api=True, metadata=None):
            event = SimpleNamespace(internal=internal, message_id='synthetic-admission', metadata=metadata or {})
            ns.update(event=event, row={'payload': {'api_turn_v1': {}} if api else {}})
            load(stamps, ns)
            self.assertIs(event.internal, True, 'execution trust must remain intact')
            return event
        ordinary = stamped()
        self.assertIsNone(ns['display_kind_for_event'](ordinary), 'API execution trust was persisted as a machinery notification')
        persistence = source('gateway/run_turn_persistence.py')
        entry = next(n for n in ast.walk(persistence) if isinstance(n, ast.FunctionDef) and n.name == '_hmwa_user_transcript_entry')
        entry.decorator_list = []
        load([StripGatewayImports().visit(entry)], ns)
        prepared = SimpleNamespace(persist_user_message=None, message_text='SYNTHETIC_INPUT', persist_user_timestamp=None,
                                   persist_user_display_kind=ns['display_kind_for_event'](ordinary), persistence_owner='SYNTHETIC_OWNER')
        row = ns['_hmwa_user_transcript_entry'](ordinary, prepared, 1)
        self.assertEqual(row['role'], 'user')
        self.assertIsNone(row.get('display_kind'))
        with ns['display_turn_scope'](ordinary.message_id, {'kind': 'hidden'}):
            self.assertEqual(ns['display_kind_for_event'](ordinary), 'hidden')
        with ns['display_turn_scope']('different-admission', {'kind': 'hidden'}):
            self.assertIsNone(ns['display_kind_for_event'](ordinary))
        self.assertEqual(ns['display_kind_for_event'](stamped(internal=True)), 'internal_notification')
        self.assertEqual(ns['display_kind_for_event'](stamped(api=False)), 'internal_notification')
        card = stamped(metadata={'display_kind': 'process_complete', 'display_text': 'SYNTHETIC_MACHINE_CARD'})
        self.assertEqual(ns['display_kind_for_event'](card), 'process_complete')
        heartbeat = stamped();heartbeat._heartbeat_session_id = 'synthetic-heartbeat'
        self.assertEqual(ns['display_kind_for_event'](heartbeat), 'internal_notification')
        spoof = SimpleNamespace(internal=True, message_id='fake', metadata={'_api_admitted_user_input': True})
        self.assertEqual(ns['display_kind_for_event'](spoof), 'internal_notification')

    def test_admitted_callbacks_deliver_public_progress_and_reasoning_on_separate_channels(self):
        tree = source('gateway/platforms/api_server_runs.py')
        execute = definitions(tree, {'_execute_run'})[0]
        call = [node for node in ast.walk(execute) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'observe_api_turn']
        self.assertEqual(len(call), 1)
        keywords = {kw.arg: kw.value for kw in call[0].keywords}
        self.assertIn('interim_assistant_callback', keywords, 'admission drops step progress')
        self.assertIn('reasoning_callback', keywords, 'admission drops structured reasoning')
        emitted, tools = [], []
        class Loop:
            def call_soon_threadsafe(self, fn, *values):
                fn(*values)
        loop = Loop()
        owner = SimpleNamespace(_run_streams={'synthetic-run': SimpleNamespace(put_nowait=emitted.append)}, _run_statuses={}, _set_run_status=lambda *a, **kw: None)
        ns = {'time': time, 'suppress': suppress, 'self': owner, 'run_id': 'synthetic-run', 'loop': loop, 'run': SimpleNamespace(put_event=emitted.append)}
        constants = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_FIXED_EVENT_FIELDS' for t in n.targets)]
        load([*constants, *definitions(tree, {'_run_event', '_make_run_event_callback'})], ns)
        ns['tool_cb'] = ns['_make_run_event_callback'](owner, 'synthetic-run', loop, _api_server=SimpleNamespace(redact_sensitive_text=lambda v, **kw: v))
        nested = [n for n in execute.body if isinstance(n, ast.FunctionDef) and n.name in {'_text_cb', '_interim_cb'}]
        load(nested, ns)
        ns['_tool_start'] = lambda *a: tools.append('start')
        ns['_tool_complete'] = lambda *a: tools.append('complete')
        sinks = {name: eval(compile(ast.Expression(node), 'observer_binding.py', 'eval'), ns) for name, node in keywords.items()}
        dispatch = {'RuntimeStoreError': type('RuntimeStoreError', (Exception,), {}), '__name__': 'public_observer_test'}
        names = {'_api_observers', '_notify_observers', 'wire_api_observers', 'publish_api_tool_event', 'publish_api_tool_progress'}
        load(definitions(source('gateway/session_api_turn.py'), names), dispatch)
        authority = SimpleNamespace(sessions={'synthetic-session': SimpleNamespace(event_stream=SimpleNamespace(lock=threading.RLock(), execution={'admission_id': 'synthetic-admission'}))}, api_observers={'synthetic-admission': [sinks]}, check_approval_generation=lambda *a: None)
        agent = SimpleNamespace(status_callback=None, interim_assistant_callback=None, tool_progress_callback=None)
        dispatch['wire_api_observers'](agent, (authority, 'synthetic-session', 1), True)
        agent.interim_assistant_callback('SYNTHETIC_PUBLIC_PROGRESS', already_streamed=True)
        agent.reasoning_callback('SYNTHETIC_REASONING')
        dispatch['publish_api_tool_event'](authority, 'synthetic-session', 1, 'tool.start', 'call-1', 'synthetic', {})
        dispatch['publish_api_tool_event'](authority, 'synthetic-session', 1, 'tool.complete', 'call-1', 'synthetic', {}, 'result')
        self.assertEqual([e['event'] for e in emitted], ['message.interim', 'reasoning.available'])
        self.assertEqual(emitted[0]['text'], 'SYNTHETIC_PUBLIC_PROGRESS')
        self.assertEqual(emitted[0]['already_streamed'], True)
        self.assertEqual(emitted[1]['text'], 'SYNTHETIC_REASONING')
        self.assertNotIn('SYNTHETIC_REASONING', emitted[0]['text'])
        self.assertEqual(tools, ['start', 'complete'])
        def stale(*a):
            raise dispatch['RuntimeStoreError']('stale')
        authority.check_approval_generation = stale
        agent.interim_assistant_callback('LATE_PROGRESS')
        agent.reasoning_callback('LATE_REASONING')
        self.assertEqual(len(emitted), 2)


if __name__ == '__main__':
    unittest.main(argv=['verify-upstream-recovery'], verbosity=2)
