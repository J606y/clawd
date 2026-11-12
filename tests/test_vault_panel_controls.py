"""Panel vault prompt bridge: PendingControls register/respond round-trip.

Covers the backend half of the clawd panel vault prompt card (branch ui-vault-prompt):
a vault prompt is registered on PendingControls, published as ``vault.request`` with
only the site/origin (never a secret), answered through ``respond(..., kind='vault')``,
and the waiting agent thread receives exactly the answer string. Uses dummy values
only (``pw-TEST``); no real vault, password, or server is touched.

Runs under pytest or unittest from the repo root.
"""
import json
import threading
import time
import unittest
from types import SimpleNamespace

from gateway.session_pending_controls import PendingControls
from gateway.session_vault_prompt import _run_vault_listener_live


class _FakeEvents:
    """Minimal stand-in for the session event stream: lock + publish.

    The lock MUST be reentrant (RLock): the real event stream's lock is one,
    because ``PendingControls.respond`` holds it while calling ``snapshot``,
    which takes it again.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.published = []

    def publish(self, session_id, payload, event_type=None):
        self.published.append((session_id, event_type, dict(payload)))


class _FakeLive:
    def __init__(self):
        self.event_stream = _FakeEvents()


class _FakeAuthority:
    def __init__(self):
        self.sessions = {"s1": _FakeLive()}


def _run_observer():
    def observer(frame):
        pass

    observer.vault_sink_kind = "run"
    return observer


def _stream_observer():
    def observer(frame):
        pass

    observer.vault_sink_kind = "stream"
    return observer


class VaultPanelControlsTest(unittest.TestCase):
    def test_register_respond_round_trip_returns_dummy_answer(self):
        events = _FakeEvents()
        controls = PendingControls(events)
        received = {}

        def responder(kind, prompt_id, answer):
            received["kind"] = kind
            received["prompt_id"] = prompt_id
            received["answer"] = answer

        prompt_id = controls.register_vault(
            "s1", 7, "vault.save_login",
            {"origin": "https://login.example.com", "site": "login.example.com"},
            timeout_s=180, responder=responder)
        self.assertTrue(prompt_id.startswith("vault-"))

        # The published vault.request carries only the site/origin — no secret.
        requests = [p for p in events.published if p[1] == "vault.request"]
        self.assertEqual(len(requests), 1)
        _, _, prompt = requests[0]
        self.assertEqual(prompt["method"], "vault.save_login")
        self.assertEqual(prompt["params"]["origin"], "https://login.example.com")
        self.assertNotIn("pw-TEST", repr(prompt))
        self.assertNotIn("password", repr(prompt["params"]))

        # The snapshot reports the prompt live while the responder is registered.
        snapshot = controls.snapshot("s1", 7)
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot[0]["kind"], "vault")

        # Answer with a dummy save_login payload; the responder gets the raw string.
        answer = '{"identifier": "user@example.com", "password": "pw-TEST"}'
        controls.respond("s1", 7, prompt_id, {"answer": answer}, kind="vault")
        self.assertEqual(received["answer"], answer)
        self.assertEqual(received["kind"], "vault")

        # Answering settles the prompt: vault.settled is published and it leaves the snapshot.
        settled = [p for p in events.published if p[1] == "vault.settled"]
        self.assertEqual(len(settled), 1)
        self.assertEqual(list(controls.snapshot("s1", 7)), [])

    def test_decline_sends_empty_answer_and_settles(self):
        events = _FakeEvents()
        controls = PendingControls(events)
        received = {}
        prompt_id = controls.register_vault(
            "s1", 3, "vault.code", {"site": "login.example.com"}, timeout_s=180,
            responder=lambda kind, pid, answer: received.setdefault("answer", answer))
        controls.respond("s1", 3, prompt_id, {"answer": ""}, kind="vault")
        self.assertEqual(received["answer"], "")
        self.assertEqual([p[1] for p in events.published], ["vault.request", "vault.settled"])

    def test_discard_vault_clears_card_on_timeout(self):
        events = _FakeEvents()
        controls = PendingControls(events)
        prompt_id = controls.register_vault(
            "s1", 1, "vault.unlock_prompt", {"backend": "bitwarden", "display_name": "Bitwarden"},
            timeout_s=120, responder=lambda kind, pid, answer: None)
        self.assertEqual(len(controls.snapshot("s1", 1)), 1)
        controls.discard_vault("s1", prompt_id)
        self.assertEqual(list(controls.snapshot("s1", 1)), [])
        self.assertEqual([p[1] for p in events.published], ["vault.request", "vault.settled"])
        # Idempotent: a second discard publishes nothing.
        controls.discard_vault("s1", prompt_id)
        self.assertEqual(len(events.published), 2)

    def test_run_listener_detector(self):
        authority = _FakeAuthority()
        # No observers at all: no run listener.
        self.assertFalse(_run_vault_listener_live(authority, "s1"))
        # A stream sink (chat/completions surface) is not a run listener.
        authority.sessions["s1"].event_stream.observers = {_stream_observer()}
        self.assertFalse(_run_vault_listener_live(authority, "s1"))
        # The runs bridge observer is.
        authority.sessions["s1"].event_stream.observers = {_run_observer()}
        self.assertTrue(_run_vault_listener_live(authority, "s1"))
        # Unknown session fails closed.
        self.assertFalse(_run_vault_listener_live(authority, "nope"))


class _FakeAuthorityWithControls:
    """Authority stand-in: real PendingControls, fake live session."""

    def __init__(self, controls, live):
        self.controls = controls
        self.sessions = {"s1": live}

    def register_vault(self, session_id, generation, method, params, *, timeout_s=None, responder=None):
        return self.controls.register_vault(session_id, generation, method, params,
                                            timeout_s=timeout_s, responder=responder)

    def discard_vault(self, session_id, prompt_id):
        self.controls.discard_vault(session_id, prompt_id)


class VaultPromptScopeTest(unittest.TestCase):
    """End-to-end of the API branch of ``vault_prompt_scope`` with dummy values."""

    def _make_turn(self, observers):
        from gateway.config import Platform

        events = _FakeEvents()
        controls = PendingControls(events)
        live = _FakeLive()
        live.event_stream.observers = set(observers)
        authority = _FakeAuthorityWithControls(controls, live)
        turn = SimpleNamespace(
            _approval_owner=(authority, "s1", 7),
            _ctx=SimpleNamespace(source=SimpleNamespace(platform=Platform.API_SERVER)),
        )
        return events, controls, turn

    def test_api_scope_save_login_round_trip_keeps_secret_out_of_events(self):
        from agent.vault_backends import unlock
        from gateway.session_vault_prompt import vault_prompt_scope

        events, controls, turn = self._make_turn([_run_observer()])
        agent = SimpleNamespace(_interrupt_requested=False)
        result = {}

        def call_save():
            with vault_prompt_scope(turn, agent):
                callback = unlock.get_save_login_prompt_callback()
                result["callback_installed"] = callback is not None
                result["answer"] = callback("https://login.example.com", "login.example.com")
            result["callback_after_scope"] = unlock.get_save_login_prompt_callback()

        worker = threading.Thread(target=call_save, daemon=True)
        worker.start()
        deadline = time.monotonic() + 10
        prompt_id = None
        while time.monotonic() < deadline:
            prompts = controls.snapshot("s1", 7)
            if prompts:
                prompt_id = prompts[0]["prompt_id"]
                break
            time.sleep(0.05)
        self.assertIsNotNone(prompt_id, "vault prompt was never registered")
        answer = json.dumps({"identifier": "user@example.com", "password": "pw-TEST"})
        controls.respond("s1", 7, prompt_id, {"answer": answer}, kind="vault")
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "save_login callback did not return after the answer")
        self.assertTrue(result["callback_installed"])
        self.assertEqual(result["answer"], {"identifier": "user@example.com", "password": "pw-TEST"})
        self.assertIsNone(result["callback_after_scope"], "scope must uninstall the callbacks")
        # The secret travels only through the responder: no published event carries it.
        self.assertNotIn("pw-TEST", repr(events.published))

    def test_api_scope_returns_fast_with_no_run_listener(self):
        from agent.vault_backends import unlock
        from gateway.session_vault_prompt import vault_prompt_scope

        # A stream sink (chat/completions surface) drops vault.request: no waiting.
        _events, controls, turn = self._make_turn([_stream_observer()])
        agent = SimpleNamespace(_interrupt_requested=False)
        started = time.monotonic()
        with vault_prompt_scope(turn, agent):
            callback = unlock.get_save_login_prompt_callback()
            self.assertIsNotNone(callback)
            self.assertIsNone(callback("https://login.example.com", "login.example.com"))
        self.assertLess(time.monotonic() - started, 5, "must fail fast, not wait the full timeout")
        self.assertEqual(list(controls.snapshot("s1", 7)), [])


if __name__ == "__main__":
    unittest.main()
