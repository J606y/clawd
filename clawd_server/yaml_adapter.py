"""Panel AST compatibility using the core's existing safe YAML dependency.

Each operation owns its parser. Validation and alias-aware redaction remain
in the panel implementation; no constructor is used to instantiate YAML tags.
"""
from io import StringIO

from ruamel.yaml import YAML
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode

# Compatibility token for compose_all(..., Loader=SafeLoader).
SafeLoader = object()


def compose_all(text, *, Loader=SafeLoader):
    if Loader is not SafeLoader:
        raise ValueError("unsupported YAML loader")
    parser = YAML(typ="safe", pure=True)
    parser.version = (1, 1)
    return parser.compose_all(text)


def serialize(node, *, allow_unicode=True):
    emitter = YAML(typ="safe", pure=True)
    emitter.allow_unicode = allow_unicode
    output = StringIO()
    emitter.serialize(node, output)
    return output.getvalue()
