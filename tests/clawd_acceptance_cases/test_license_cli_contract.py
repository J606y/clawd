"""License CLI contract. Not executed by the authoring turn.

Reads generate.py text and the acceptance argv builder. Does not import the
extractor, run it, or treat a parsed inventory as a legal pass.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "clawd_acceptance"))

import execute  # noqa: E402
from isolation import Layout  # noqa: E402
from stages import license_argv  # noqa: E402

GENERATOR = REPO / "scripts" / "clawd_inventory" / "generate.py"
SUFFIXES = (
    "ui-tui/packages/hermes-ink/package.json",
    "apps/panel/package.json",
    "apps/shared/package.json",
    "ui-tui/package.json",
)


class LicenseCliContractTests(unittest.TestCase):
    def test_real_flags_are_past_the_truncated_source_window(self) -> None:
        text = GENERATOR.read_text(encoding="utf-8")
        self.assertGreater(len(text), 20000)
        self.assertNotIn('"--checkout"', text[:20000])
        self.assertNotIn('"--output"', text[:20000])
        flags = execute.argparse_flags(text)
        self.assertIn("--checkout", flags)
        self.assertIn("--output", flags)
        self.assertIn("--offline", flags)
        self.assertIn("--input", flags)
        self.assertNotIn("--legal-pass", flags)

    def test_entry_uses_only_real_flags_and_absolute_capture(self) -> None:
        layout = Layout(REPO / "artifacts" / "final-acceptance")
        argv = license_argv(REPO / ".venv" / "bin" / "python", REPO, layout.logs / "license-inventory.json")
        flags = execute.argparse_flags(GENERATOR.read_text(encoding="utf-8"))
        used = [item.split("=", 1)[0] for item in argv if item.startswith("-")]
        self.assertTrue(set(used) <= flags)
        self.assertEqual(used, ["--checkout", "--output"])
        self.assertTrue(Path(argv[-1]).is_absolute())
        self.assertEqual(Path(argv[-1]).name, "license-inventory.json")
        self.assertEqual(execute._license_argv(REPO, layout), argv)
        ok, detail = execute.license_signature_ok(REPO, argv)
        self.assertTrue(ok, detail)
        self.assertNotIn("法律", detail)
        bad = [*argv, "--legal-pass"]
        refused, reason = execute.license_signature_ok(REPO, bad)
        self.assertFalse(refused)
        self.assertIn("does not define", reason)
        relative = license_argv(REPO / ".venv" / "bin" / "python", REPO, Path("license-inventory.json"))
        self.assertFalse(execute.license_signature_ok(REPO, relative)[0])

    def test_parse_success_does_not_grant_legal_review(self) -> None:
        source = (REPO / "scripts" / "clawd_acceptance" / "execute.py").read_text(encoding="utf-8")
        self.assertNotIn("inventory_argv_ok", source)
        self.assertIn("legal_review\": \"blocked\"", source)
        document = {
            "network_audit": False,
            "compliance_conclusion": "not_determined",
            "packaged_membership": "unknown",
            "errors": False,
            "dependencies": [],
            "components": [{"path": suffix, "license": "unknown"} for suffix in SUFFIXES],
        }
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "license-inventory.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            classified = execute.classify_license_inventory(path)
        self.assertEqual(classified["extractor_parse"], "pass")
        self.assertEqual(classified["legal_review"], "blocked")
        self.assertNotEqual(classified["status"], "pass")
        self.assertEqual(classified["exit_code"], 2)
        self.assertTrue(classified["unknown_scopes"])
