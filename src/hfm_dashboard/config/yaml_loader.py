"""Strict YAML reading shared by the configuration loaders."""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys (PyYAML silently keeps the last one)."""


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if isinstance(key, (dict, list)):
            # An unquoted {{NAME}} is YAML flow-mapping syntax and lands here as a mapping key.
            raise yaml.constructor.ConstructorError(
                None, None,
                "a mapping or list cannot be a key - is a {{PLACEHOLDER}} left unquoted? "
                "Quote placeholder values: \"{{NAME}}\"",
                key_node.start_mark,
            )
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_mapping(text: str, source: str, error: type[Exception]) -> dict[str, Any]:
    """Parse YAML text whose top level must be a mapping; raise ``error`` otherwise."""
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise error(f"{source}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise error(f"{source}: top level must be a mapping")
    return data


def format_validation_error(exc: ValidationError, source: str) -> str:
    """List problems by path only; input values are left out so real names never reach a log."""
    lines = [
        f"  {'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in exc.errors()
    ]
    return f"{source}: {len(lines)} problem(s)\n" + "\n".join(lines)
