"""Fail when the two live_task_store.py copies diverge.

Compares file bytes. A shared content change that keeps both copies
identical must stay green; do not pin a hash.
"""
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_STORE = REPO_ROOT.parents[1] / "clawd_server" / "live_task_store.py"
PLUGIN_STORE = REPO_ROOT / "plugins" / "browser-window-guard" / "live_task_store.py"


class LiveTaskStoreIdentityTests(unittest.TestCase):
    def test_copies_are_byte_identical(self):
        paths = (CORE_STORE, PLUGIN_STORE)
        missing = [path for path in paths if not path.is_file()]
        if missing:
            self.fail(
                "live_task_store.py copies unavailable:\n"
                f"{CORE_STORE}\n"
                f"{PLUGIN_STORE}"
            )
        core_bytes = CORE_STORE.read_bytes()
        plugin_bytes = PLUGIN_STORE.read_bytes()
        if core_bytes != plugin_bytes:
            self.fail(
                "live_task_store.py copies differ:\n"
                f"{CORE_STORE}\n"
                f"{PLUGIN_STORE}"
            )


if __name__ == "__main__":
    unittest.main()
