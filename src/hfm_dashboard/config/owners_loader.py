"""Load owners.yaml. A missing file is not an error (the owner list is still open, Q32): the caller
gets None and notices are logged as "owner list missing". A present file must be complete:
no placeholders, valid addresses, known components, every enabled component covered."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from hfm_dashboard.config.environment_loader import find_placeholders
from hfm_dashboard.config.yaml_loader import format_validation_error, load_mapping
from hfm_dashboard.domain.owners import OwnersConfig, check_against_rules
from hfm_dashboard.domain.rules import Rules


class OwnersError(Exception):
    """owners.yaml exists but is invalid. Messages name paths, never the addresses."""


def parse_owners(text: str, rules: Rules, source: str = "owners.yaml") -> OwnersConfig:
    data = load_mapping(text, source, OwnersError)
    unfilled = find_placeholders(data)
    if unfilled:
        raise OwnersError(f"{source}: {len(unfilled)} placeholder(s) not filled in:\n"
                          + "\n".join(f"  {u}" for u in unfilled))
    try:
        owners = OwnersConfig.model_validate(data)
    except ValidationError as exc:
        raise OwnersError(format_validation_error(exc, source)) from exc
    problems = check_against_rules(owners, rules)
    if problems:
        raise OwnersError(f"{source}: {len(problems)} problem(s) against rules.yaml\n"
                          + "\n".join(f"  {p}" for p in problems))
    return owners


def load_owners(config_dir: str | Path, rules: Rules) -> OwnersConfig | None:
    path = Path(config_dir) / rules.notices.owners_file
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OwnersError(f"cannot read {path.name}: {exc.strerror}") from exc
    return parse_owners(text, rules, source=path.name)
