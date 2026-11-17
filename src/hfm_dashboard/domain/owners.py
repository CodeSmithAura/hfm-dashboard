"""Typed model of owners.yaml: who receives the notices for each component."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from hfm_dashboard.domain.rules import Rules

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(value: str) -> str:
    if not _EMAIL.match(value):
        raise ValueError("not an email address")
    return value


Email = Annotated[str, AfterValidator(_email)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Contacts(_Strict):
    owners: list[Email] = Field(min_length=1)
    managers: list[Email] = Field(default_factory=list)


class OwnersConfig(_Strict):
    monitoring_team: list[Email] = Field(min_length=1)
    default: Contacts | None = None
    components: dict[str, Contacts] = Field(default_factory=dict)

    def contacts_for(self, component: str) -> Contacts | None:
        return self.components.get(component, self.default)


def check_against_rules(owners: OwnersConfig, rules: Rules) -> list[str]:
    problems = [f"components.{cid}: no such component in rules.yaml"
                for cid in owners.components if cid not in rules.components]
    if owners.default is None:
        problems += [f"components: no owners for enabled component {cid} (and no default)"
                     for cid in rules.enabled_components() if cid not in owners.components]
    return problems
