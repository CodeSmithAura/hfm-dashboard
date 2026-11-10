"""Typed, validated model of environments.yaml, and its cross-checks against rules.yaml.

environments.yaml holds the real hosts, ports, service names and secret NAMES for each environment
(never secret values). It lives only on the monitoring server; the repository keeps the template.

One monitoring service watches every environment in the file (dashboard per-environment view).
An environment may be smaller than the production topology in rules.yaml: a rules.yaml node that an
environment does not declare is simply not monitored there (decision D18). Typos are still caught:
every declared node must be used, every component needs at least one node, and every node of a
component with a windows_service check needs a service name.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hfm_dashboard.domain.rules import Rules

NonEmpty = Annotated[str, Field(min_length=1)]
Port = Annotated[int, Field(ge=1, le=65535)]

PLACEHOLDER = re.compile(r"\{\{\s*[A-Za-z0-9_]+\s*\}\}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Node(_Strict):
    host: NonEmpty
    role: Literal["database", "web", "hfm"]


class LoadBalancer(_Strict):
    url: NonEmpty
    paths: dict[str, NonEmpty]

    @field_validator("url")
    @classmethod
    def _http_url(cls, url: str) -> str:
        if not url.startswith(("https://", "http://")):
            raise ValueError("url must start with https:// or http://")
        return url.rstrip("/")


class EnvironmentNotices(_Strict):
    smtp_relay: NonEmpty
    from_address: NonEmpty


class Environment(_Strict):
    display_name: NonEmpty
    notices_enabled: bool | None = None   # optional; rules.yaml environments.<name> is authoritative
    timezone_note: str = ""
    load_balancer: LoadBalancer
    nodes: dict[str, Node] = Field(min_length=1)
    ports: dict[str, Port]
    services: dict[str, dict[str, NonEmpty]]   # component -> node -> Windows service name
    databases: list[NonEmpty]
    notices: EnvironmentNotices
    secrets: dict[str, NonEmpty]               # logical name -> secret-store NAME (never a value)

    def nodes_of(self, component_id: str, rules: Rules) -> list[str]:
        """The rules.yaml nodes of a component that exist in this environment, in rules order."""
        return [n for n in rules.components[component_id].nodes if n in self.nodes]

    def service_name(self, component_id: str, node: str) -> str:
        """Windows service name, looked up per node (never derived: suffixes differ per node)."""
        return self.services[component_id][node]


class Monitoring(_Strict):
    monitor_host: NonEmpty


class EnvironmentsConfig(_Strict):
    environments: dict[str, Environment] = Field(min_length=1)
    monitoring: Monitoring


def check_against_rules(config: EnvironmentsConfig, rules: Rules) -> list[str]:
    """Return every inconsistency between environments.yaml and rules.yaml (empty = consistent)."""
    problems: list[str] = []
    enabled = rules.enabled_components()

    for env_name, env in config.environments.items():
        where = f"environments.{env_name}"

        env_rules = rules.environments.get(env_name)
        if env_rules is None:
            problems.append(f"{where}: not listed under environments in rules.yaml "
                            f"{sorted(rules.environments)}")
        elif env.notices_enabled is not None and env.notices_enabled != env_rules.notices_enabled:
            problems.append(f"{where}.notices_enabled: {env.notices_enabled} disagrees with rules.yaml "
                            f"({env_rules.notices_enabled}); rules.yaml decides - align or remove it")

        used_nodes: set[str] = set()
        for cid, comp in enabled.items():
            present = env.nodes_of(cid, rules)
            used_nodes.update(present)
            if not present:
                problems.append(f"{where}: component {cid} has none of its nodes {comp.nodes} "
                                f"in this environment")
                continue
            if "windows_service" in comp.checks:
                names = env.services.get(cid, {})
                for node in present:
                    if node not in names:
                        problems.append(f"{where}.services.{cid}: no service name for node {node}")

        for node in env.nodes:
            if node not in used_nodes:
                problems.append(f"{where}.nodes.{node}: not used by any enabled component in rules.yaml")

        for cid, names in env.services.items():
            if cid not in rules.components:
                problems.append(f"{where}.services.{cid}: no such component in rules.yaml")
                continue
            for node in names:
                if node not in env.nodes:
                    problems.append(f"{where}.services.{cid}.{node}: node not declared in this environment")
                elif node not in rules.components[cid].nodes:
                    problems.append(f"{where}.services.{cid}.{node}: component {cid} does not run on "
                                    f"{node} in rules.yaml")
    return problems
