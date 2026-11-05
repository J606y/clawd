"""Independent parent-review YAML security regressions; synthetic data only."""
import unittest
from unittest.mock import patch
from sidecar import hermes_panel_sidecar as sidecar


@unittest.skipIf(sidecar.yaml is None, 'Requires isolated locked YAML environment')
class ParentYamlSecurityTests(unittest.TestCase):
    def test_sensitive_alias_used_as_mapping_key_cannot_leak(self):
        source = 'api_key: &private SYNTHETIC_ALIAS_VALUE\nmapping:\n  ? *private\n  : visible\n'
        output = sidecar._redact_yaml(source)
        self.assertNotIn('SYNTHETIC_ALIAS_VALUE', output)
        self.assertIn('visible', output)

    def test_unknown_tags_in_yaml_namespace_are_rejected(self):
        for tag in ['!!not-a-core-type', '!!python/object:synthetic.Payload']:
            with self.subTest(tag=tag):
                with self.assertRaises(sidecar.ApiError) as caught:
                    sidecar._redact_yaml(f'value: {tag} opaque\n')
                self.assertEqual(caught.exception.code, 'config_parse_failed')

    def test_oversized_input_is_rejected_before_parser_call(self):
        with patch.object(sidecar.yaml, 'compose_all', side_effect=AssertionError('must not parse oversized input')) as parser:
            with self.assertRaises(sidecar.ApiError) as caught:
                sidecar._redact_yaml('name: ' + 'x' * 1_048_576)
            self.assertEqual(caught.exception.code, 'config_parse_failed')
            self.assertEqual(parser.call_count, 0, 'Oversized input reached the parser')

    def test_redacting_alias_keys_must_not_create_duplicate_output_keys(self):
        source = 'api_key: &a FIRST_OPAQUE\nsecret: &b SECOND_OPAQUE\nmapping:\n  ? *a\n  : first\n  ? *b\n  : second\n'
        with self.assertRaises(sidecar.ApiError) as caught:
            sidecar._redact_yaml(source)
        self.assertEqual(caught.exception.code, 'config_parse_failed')

    def test_input_limit_counts_utf8_bytes(self):
        with patch.object(sidecar.yaml, 'compose_all', side_effect=AssertionError('must not parse oversized bytes')) as parser:
            with self.assertRaises(sidecar.ApiError) as caught:
                sidecar._redact_yaml('name: ' + '汉' * 400_000)
            self.assertEqual(caught.exception.code, 'config_parse_failed')
            self.assertEqual(parser.call_count, 0)

    def test_recursive_alias_is_rejected_without_exposing_input(self):
        with self.assertRaises(sidecar.ApiError) as caught:
            sidecar._redact_yaml('root: &r {self: *r, api_key: SYNTHETIC_CYCLE_SECRET}\n')
        self.assertEqual(caught.exception.code, 'config_parse_failed')
        self.assertNotIn('SYNTHETIC_CYCLE_SECRET', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
