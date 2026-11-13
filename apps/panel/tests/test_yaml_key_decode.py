import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from clawd_server import panel_core as sidecar


class YamlKeyDecodeTests(unittest.TestCase):
    def test_escape_failure_keeps_the_key_and_logs_the_type(self):
        with self.assertLogs(sidecar.log, level=logging.WARNING) as logs:
            with mock.patch.object(sidecar.re, "sub", side_effect=ValueError("bad")):
                kept = sidecar._clean_yaml_key("token\\x41")
        self.assertEqual(kept, "token\\x41")
        self.assertTrue(any("ValueError" in line for line in logs.output))
        self.assertFalse(any("token" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
