import logging
import unittest
from unittest import mock

from sidecar import hermes_panel_sidecar as sidecar


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
