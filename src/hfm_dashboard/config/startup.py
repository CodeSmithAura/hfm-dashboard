"""Start-up configuration gate (hard rule 6): load rules.yaml, then environments.yaml.

Any problem raises ``ConfigurationError`` and the program must not start.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfm_dashboard.config.environment_loader import EnvironmentConfigError, load_environments
from hfm_dashboard.config.rules_loader import RulesError, load_rules
from hfm_dashboard.domain.environment import EnvironmentsConfig
from hfm_dashboard.domain.rules import Rules

RULES_FILE = "rules.yaml"
ENVIRONMENTS_FILE = "environments.yaml"


class ConfigurationError(Exception):
    """The configuration is invalid; the program refuses to start."""


@dataclass(frozen=True, slots=True)
class Configuration:
    rules: Rules
    environments: EnvironmentsConfig


def load_configuration(config_dir: str | Path) -> Configuration:
    config_dir = Path(config_dir)
    try:
        rules = load_rules(config_dir / RULES_FILE)
        environments = load_environments(config_dir / ENVIRONMENTS_FILE, rules)
    except (RulesError, EnvironmentConfigError) as exc:
        raise ConfigurationError(f"refusing to start: {exc}") from exc
    return Configuration(rules=rules, environments=environments)
