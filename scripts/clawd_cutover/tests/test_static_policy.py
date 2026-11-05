"""Static source checks. Not executed in the authoring session."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    "shutil.rmtree",
    "os.rmdir",
    "os.removedirs",
    "subprocess",
    "os.system",
    "importlib",
)


class StaticPolicyTest(unittest.TestCase):
    def test_python_sources_do_not_remove_directories_or_spawn(self) -> None:
        files = list(ROOT.glob("*.py"))
        self.assertGreaterEqual(len(files), 8)
        for path in files:
            text = path.read_text(encoding="utf-8")
            for item in FORBIDDEN:
                self.assertNotIn(item, text, f"{path.name} contains {item}")

    def test_templates_are_marked_unapplied(self) -> None:
        for name in ("clawd-panel.service.example", "clawd-nginx.conf.example"):
            text = (ROOT / "templates" / name).read_text(encoding="utf-8")
            self.assertIn("NOT_APPLIED", text)
            self.assertNotIn("/root/.hermes", text)


if __name__ == "__main__":
    unittest.main()
