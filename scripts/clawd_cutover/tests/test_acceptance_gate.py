"""Acceptance-report consumer tests. Not executed in the authoring session.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import acceptance_gate


class AcceptanceGateTests(unittest.TestCase):
    def test_short_pass_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "fake.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "final_acceptance": True,
                        "covers_cutover": True,
                        "producer": "scripts/clawd_acceptance",
                    }
                ),
                encoding="utf-8",
            )
            result = acceptance_gate.evaluate_report(path, mode="apply")
            self.assertEqual(result["status"], "rejected")
            self.assertTrue(result["blocks_apply"])
            self.assertIn("kind", result["reasons"])

    def test_prepare_wrapper_is_not_green(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "prepare-plan.json"
            path.write_text(json.dumps({"acceptance_exit": 0, "report": {"status": "passed"}}), encoding="utf-8")
            result = acceptance_gate.evaluate_report(path, mode="apply")
            self.assertEqual(result["status"], "rejected")
            self.assertIn("prepare-plan-wrapper", result["reasons"])


if __name__ == "__main__":
    unittest.main()
