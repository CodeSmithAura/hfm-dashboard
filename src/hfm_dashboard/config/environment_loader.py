"""Load and validate environments.yaml. The program refuses to start if this raises.

Order of checks:
1. YAML is readable, has no duplicate keys, top level is a mapping.
2. No ``{{PLACEHOLDER}}`` is left in any value (hard rule 6). Comments are ignored, so the
   template's explanatory comments do not block start-up.
3. Structure and types (unknown keys, ports 1..65535, roles, ...).
4. Consistency with rules.yaml (components, nodes, service names, environment names).

Error messages name paths and placeholder names only, never the filled-in values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hfm_dashboard.config.yaml_loader import format_validation_error, load_mapping
from hfm_dashboard.domain.environment import PLACEHOLDER, EnvironmentsConfig, check_against_rules
from hfm_dashboard.domain.rules import Rules


class EnvironmentConfigError(Exception):
    """environments.yaml is missing, unreadable, unfilled or inconsistent with rules.yaml."""


def find_placeholders(data: Any, path: str = "") -> list[str]:
    """Return ``path: {{NAME}}`` for every string value (or key) still holding a placeholder."""
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and PLACEHOLDER.search(key):
                found.append(f"{child} (key)")
            found.extend(find_placeholders(value, child))
    elif isinstance(data, list):
        for i, value in enumerate(data):
            found.extend(find_placeholders(value, f"{path}[{i}]"))
    elif isinstance(data, str):
        names = PLACEHOLDER.findall(data)
        if names:
            found.append(f"{path}: {', '.join(names)}")
    return found


def parse_environments(text: str, rules: Rules, source: str = "environments.yaml") -> EnvironmentsConfig:
    data = load_mapping(text, source, EnvironmentConfigError)

    unfilled = find_placeholders(data)
    if unfilled:
        raise EnvironmentConfigError(
            f"{source}: {len(unfilled)} placeholder(s) not filled in - replace them with the real "
            f"values for this environment:\n" + "\n".join(f"  {u}" for u in unfilled)
        )

    try:
        config = EnvironmentsConfig.model_validate(data)
    except ValidationError as exc:
        raise EnvironmentConfigError(format_validation_error(exc, source)) from exc

    problems = check_against_rules(config, rules)
    if problems:
        raise EnvironmentConfigError(
            f"{source}: {len(problems)} problem(s) against rules.yaml\n"
            + "\n".join(f"  {p}" for p in problems)
        )
    return config


def load_environments(path: str | Path, rules: Rules) -> EnvironmentsConfig:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EnvironmentConfigError(
            f"cannot read {path.name}: {exc.strerror} "
            f"(copy environments.template.yaml to {path.name} and fill it in)"
        ) from exc
    return parse_environments(text, rules, source=path.name)
