"""Load and validate config/rules.yaml. The program refuses to start if this raises."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from hfm_dashboard.config.yaml_loader import format_validation_error, load_mapping
from hfm_dashboard.domain.rules import Rules


class RulesError(Exception):
    """rules.yaml is missing, unreadable or invalid."""


def parse_rules(text: str, source: str = "rules.yaml") -> Rules:
    data = load_mapping(text, source, RulesError)
    try:
        return Rules.model_validate(data)
    except ValidationError as exc:
        raise RulesError(format_validation_error(exc, source)) from exc


def load_rules(path: str | Path) -> Rules:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesError(f"cannot read {path.name}: {exc.strerror}") from exc
    return parse_rules(text, source=path.name)
