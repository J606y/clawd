"""Comprehensive standalone tests for YAML redaction security, structure preservation, and error handling."""
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
import sys
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from clawd_server import panel_core as sidecar_mod


class YamlRedactionStandaloneTests(unittest.TestCase):
    def test_basic_key_redaction(self):
        source = (
            'api_key: secret-value\n'
            'normal_field: 12345\n'
        )
        res = sidecar_mod._redact_yaml(source)
        self.assertNotIn('secret-value', res)
        self.assertIn('normal_field: 12345', res)
        self.assertIn('<已隐藏>', res)

    def test_quoted_comma_secret_never_partially_returned(self):
        source = 'env: {api_key: "opaque-first,opaque-rest-secret", normal: 123}\n'
        res = sidecar_mod._redact_yaml(source)
        self.assertNotIn('opaque-first', res)
        self.assertNotIn('opaque-rest-secret', res)
        self.assertIn('normal: 123', res)

    def test_plain_scalar_apostrophe(self):
        source = "display_title: user's panel\n"
        res = sidecar_mod._redact_yaml(source)
        self.assertIn("user's panel", res)

    def test_shared_reference_alias_redacted_both_sides(self):
        # Ordinary key defines anchor, sensitive key references alias -> secret must not leak at either place
        source = (
            'safe_key: &anchor shared-secret-value\n'
            'api_key: *anchor\n'
        )
        res = sidecar_mod._redact_yaml(source)
        self.assertNotIn('shared-secret-value', res)
        self.assertIn('safe_key: <已隐藏>', res)
        self.assertIn('api_key: <已隐藏>', res)

    def test_alias_to_secret_key_redacted(self):
        source = (
            'tokens: {api_key: &anchor alias-secret-val}\n'
            'public_ref: *anchor\n'
        )
        res = sidecar_mod._redact_yaml(source)
        self.assertNotIn('alias-secret-val', res)
        self.assertIn('<已隐藏>', res)

    def test_nested_and_list_flow_redaction(self):
        flow_map = 'env: {api_key: "flow-secret-token-12345", normal: 123}'
        flow_list = '- {secret: list-secret-token-54321, name: safe}'
        r_map = sidecar_mod._redact_yaml(flow_map)
        r_list = sidecar_mod._redact_yaml(flow_list)
        self.assertNotIn('flow-secret-token-12345', r_map)
        self.assertNotIn('list-secret-token-54321', r_list)
        self.assertIn('normal: 123', r_map)
        self.assertIn('name: safe', r_list)

    def test_escape_encoded_keys_redacted(self):
        escape_key = '"api\\x5fkeys":\n  - escaped-secret-val-999\n'
        res = sidecar_mod._redact_yaml(escape_key)
        self.assertNotIn('escaped-secret-val-999', res)
        self.assertIn('<已隐藏>', res)

    def test_syntax_error_structured_apierror_no_raw_leak(self):
        malformed = '::: invalid ::: yaml {{[[\nunclosed_secret: raw-opaque-secret-should-not-leak\n'
        with self.assertRaises(sidecar_mod.ApiError) as cm:
            sidecar_mod._redact_yaml(malformed)
        err = cm.exception
        self.assertEqual(err.status, 500)
        self.assertEqual(err.code, 'config_parse_failed')
        self.assertNotIn('raw-opaque-secret-should-not-leak', str(err.message))
        self.assertNotIn('raw-opaque-secret-should-not-leak', getattr(err, 'code', ''))

    def test_multi_document_rejection(self):
        multi_doc = (
            '---\n'
            'doc1: safe_value\n'
            '---\n'
            'doc2: secret_value\n'
        )
        with self.assertRaises(sidecar_mod.ApiError) as cm:
            sidecar_mod._redact_yaml(multi_doc)
        err = cm.exception
        self.assertEqual(err.status, 500)
        self.assertEqual(err.code, 'config_parse_failed')

    def test_custom_tag_rejection(self):
        custom_tag = 'exploit: !ExploitPayload "dangerous-payload"\n'
        with self.assertRaises(sidecar_mod.ApiError) as cm:
            sidecar_mod._redact_yaml(custom_tag)
        err = cm.exception
        self.assertEqual(err.status, 500)
        self.assertEqual(err.code, 'config_parse_failed')
        self.assertNotIn('dangerous-payload', str(err.message))

    def test_duplicate_key_rejection(self):
        dup = (
            'dup_key: value1\n'
            'dup_key: value2\n'
        )
        with self.assertRaises(sidecar_mod.ApiError) as cm:
            sidecar_mod._redact_yaml(dup)
        err = cm.exception
        self.assertEqual(err.status, 500)
        self.assertEqual(err.code, 'config_parse_failed')

    def test_complex_mapping_key_rejection(self):
        complex_key = '[1, 2]: complex_val\n'
        with self.assertRaises(sidecar_mod.ApiError) as cm:
            sidecar_mod._redact_yaml(complex_key)
        err = cm.exception
        self.assertEqual(err.status, 500)
        self.assertEqual(err.code, 'config_parse_failed')

    def test_depth_and_node_limit_rejection(self):
        bomb = (
            'a: &a ["lol","lol","lol","lol","lol","lol","lol","lol","lol"]\n'
            'b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]\n'
            'c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]\n'
            'd: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]\n'
            'e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]\n'
        )
        with self.assertRaises(sidecar_mod.ApiError) as cm:
            sidecar_mod._redact_yaml(bomb)
        err = cm.exception
        self.assertEqual(err.status, 500)
        self.assertEqual(err.code, 'config_parse_failed')

    def test_empty_and_whitespace_yaml(self):
        self.assertEqual(sidecar_mod._redact_yaml(''), '')
        self.assertEqual(sidecar_mod._redact_yaml('   \n  \n'), '   \n  \n')

    def test_get_config_contract_and_structure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            cfg.write_text('server:\n  port: 8080\n  api_key: secret-key-in-file\n')
            with patch.object(sidecar_mod, "HERMES_HOME", Path(td)):
                data = sidecar_mod.get_config()
                self.assertIn("path", data)
                self.assertIn("yaml", data)
                self.assertIn("modifiedAt", data)
                self.assertEqual(data["path"], str(cfg))
                self.assertNotIn("secret-key-in-file", data["yaml"])
                self.assertIn("port: 8080", data["yaml"])
                self.assertIn("<已隐藏>", data["yaml"])

    def test_missing_dependency_behavior(self):
        saved_yaml = sidecar_mod.yaml
        try:
            sidecar_mod.yaml = None
            with self.assertRaises(sidecar_mod.ApiError) as cm:
                sidecar_mod._redact_yaml("a: 1")
            err = cm.exception
            self.assertEqual(err.status, 503)
            self.assertEqual(err.code, "yaml_parser_unavailable")
        finally:
            sidecar_mod.yaml = saved_yaml


if __name__ == '__main__':
    unittest.main()
