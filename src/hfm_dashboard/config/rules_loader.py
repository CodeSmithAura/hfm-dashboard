"""Load and validate config/rules.yaml. The program refuses to start if this raises."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from hfm_dashboard.domain.rules import Rules


class RulesError(Exception):
    """rules.yaml is missing, unreadable or invalid."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys (PyYAML silently keeps the last one)."""


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def parse_rules(text: str, source: str = "rules.yaml") -> Rules:
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise RulesError(f"{source}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RulesError(f"{source}: top level must be a mapping")
    try:
        return Rules.model_validate(data)
    except ValidationError as exc:
        lines = [
            f"  {'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise RulesError(f"{source}: {len(lines)} problem(s)\n" + "\n".join(lines)) from exc


def load_rules(path: str | Path) -> Rules:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesError(f"cannot read {path.name}: {exc.strerror}") from exc
    return parse_rules(text, source=path.name)
