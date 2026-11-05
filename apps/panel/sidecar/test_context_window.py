"""grok-4.7 圆环只读该模型的覆盖；其他模型仍用日志里的窗口。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sidecar import hermes_panel_sidecar as s

OVERRIDES = {
    "custom": {"grok-4.7": {"context_window": 500000}},
    "sub2api-grok": {"grok-4.7": {"context_window": 500000}},
}


class ContextWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []

    def write(self, sid, model, provider, hardcoded):
        call = (
            f"2026-10-28 22:05:32,418 INFO [{sid}] agent.conversation_loop: "
            f"API call #1: model={model} provider={provider} in=1000 out=10 total=1010 "
            f"latency=1.0s cache=0/1000 (0%)\n"
        )
        notes = "".join(
            f"2026-10-28 21:45:56,066 INFO agent.model_metadata: Using hardcoded context length {n} for model '{name}'\n"
            for name, n in hardcoded
        )
        (self.root / "agent.log").write_text(notes + call)

    def query(self, sid, model_cfg=None, overrides=OVERRIDES):
        def fake_get(key):
            self.calls.append(key)
            if key == "model":
                return model_cfg if model_cfg is not None else {}
            if key == "model_overrides":
                return overrides
            raise AssertionError(key)

        with patch.object(s, "LOG_DIR", self.root), patch.object(s, "config_get_json", fake_get), patch.object(
            s, "_session_totals", return_value=None
        ):
            return s.get_context(sid)

    def test_grok47_uses_its_provider_override_not_the_256k_log(self):
        self.write("ctxwin-grok47", "grok-4.7", "custom", [("grok-4.7", "256,000"), ("gemini-3.8-flash", "1,048,576")])
        got = self.query("ctxwin-grok47")
        self.assertEqual(got["window"], 500000)
        self.assertEqual(got["source"], "override")
        self.assertEqual(got["used"], 1000)

    def test_other_models_keep_their_own_log_window(self):
        self.write("ctxwin-gemini1", "gemini-3.8-flash", "custom", [("grok-4.7", "256,000"), ("gemini-3.8-flash", "1,048,576")])
        gemini = self.query("ctxwin-gemini1")
        self.assertEqual(gemini["window"], 1048576)
        self.assertEqual(gemini["source"], "default")
        self.write("ctxwin-grok420", "grok-4.20-0309-reasoning", "custom", [("grok-4.20-0309-reasoning", "2,000,000")])
        other = self.query("ctxwin-grok420")
        self.assertEqual(other["window"], 2000000)
        self.assertEqual(other["source"], "default")
        self.assertNotIn("model_overrides", self.calls)

    def test_missing_or_other_provider_override_falls_back_to_log(self):
        self.write("ctxwin-grok47b", "grok-4.7", "sub2api-gemini", [("grok-4.7", "256,000")])
        got = self.query("ctxwin-grok47b", overrides={"custom": {"grok-4.7": {"context_window": 500000}}})
        self.assertEqual(got["window"], 256000)
        self.assertEqual(got["source"], "default")

    def test_global_context_length_still_applies_to_every_model(self):
        self.write("ctxwin-gemini2", "gemini-3.8-flash", "custom", [("gemini-3.8-flash", "1,048,576")])
        got = self.query("ctxwin-gemini2", model_cfg={"context_length": 111111})
        self.assertEqual(got["window"], 111111)
        self.assertEqual(got["source"], "config")
        self.assertNotIn("model_overrides", self.calls)


if __name__ == "__main__":
    unittest.main()
