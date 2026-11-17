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


class WinRM(_Strict):
    """How the monitoring server reaches the nodes' WinRM listener (read-only queries only)."""

    port: Port
    use_ssl: bool
    auth: Literal["negotiate", "kerberos"]
    cert_validation: bool = True


class Weblogic(_Strict):
    """WebLogic REST management API on the Admin Server, and which server runs where."""

    admin_url: NonEmpty
    cert_validation: bool = True
    servers: dict[str, dict[str, NonEmpty]]    # component -> node -> WebLogic server name

    @field_validator("admin_url")
    @classmethod
    def _http_url(cls, url: str) -> str:
        if not url.startswith(("https://", "http://")):
            raise ValueError("admin_url must start with https:// or http://")
        return url.rstrip("/")


class UrlProbeTarget(_Strict):
    port: NonEmpty    # key into ``ports`` (e.g. foundation, hfm_web, fr_web)
    path: NonEmpty

    @field_validator("path")
    @classmethod
    def _absolute(cls, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("path must start with /")
        return path


class UrlProbes(_Strict):
    """Per-node probes go straight to each web node's managed server (OHS would balance across
    both nodes and hide which one failed). Load balancer probes use ``load_balancer.url`` + path."""

    scheme: Literal["http", "https"]
    cert_validation: bool = True
    probes: dict[str, UrlProbeTarget]   # probe name (rules.yaml degraded_after_seconds) -> target


class Sql(_Strict):
    """How the monitoring server connects to SQL Server (read-only health queries)."""

    port: NonEmpty                       # key into ``ports``
    auth: Literal["windows", "sql"]      # windows: the service's gMSA; sql: login from secrets
    encrypt: bool = True
    trust_server_certificate: bool = False


class EnvironmentNotices(_Strict):
    smtp_relay: NonEmpty
    smtp_port: Port
    starttls: bool = True             # upgrade the relay connection to TLS (relay set-up, A13)
    from_address: NonEmpty
    dashboard_url: str = ""           # link in notice emails once the dashboard exists


class Environment(_Strict):
    display_name: NonEmpty
    notices_enabled: bool | None = None   # optional; rules.yaml environments.<name> is authoritative
    timezone_note: str = ""
    load_balancer: LoadBalancer
    nodes: dict[str, Node] = Field(min_length=1)
    ports: dict[str, Port]
    winrm: WinRM
    weblogic: Weblogic
    url_probes: UrlProbes
    sql: Sql
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

    def weblogic_server(self, component_id: str, node: str) -> str:
        """WebLogic server name of a component on a node (e.g. a managed server per web node)."""
        return self.weblogic.servers[component_id][node]

    def node_probe_url(self, probe: str, node: str) -> str:
        target = self.url_probes.probes[probe]
        return f"{self.url_probes.scheme}://{self.nodes[node].host}:{self.ports[target.port]}{target.path}"

    def lb_probe_url(self, probe: str) -> str:
        return f"{self.load_balancer.url}{self.url_probes.probes[probe].path}"

    def names_for(self, check_kind: str) -> tuple[str, dict[str, dict[str, str]]]:
        """(config path, component -> node -> name) for checks that need a name per node."""
        if check_kind == "windows_service":
            return "services", self.services
        if check_kind == "weblogic_server":
            return "weblogic.servers", self.weblogic.servers
        raise KeyError(check_kind)


# Checks whose target on each node is named in environments.yaml
NAMED_CHECKS = ("windows_service", "weblogic_server")


class Monitoring(_Strict):
    monitor_host: NonEmpty
    dashboard_bind: NonEmpty     # interface the dashboard listens on (host name or address)
    dashboard_port: Port


class EnvironmentsConfig(_Strict):
    environments: dict[str, Environment] = Field(min_length=1)
    monitoring: Monitoring | None = None   # required for the service; not needed to check one environment


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
            for kind in NAMED_CHECKS:
                if kind not in comp.checks:
                    continue
                path, table = env.names_for(kind)
                names = table.get(cid, {})
                for node in present:
                    if node not in names:
                        problems.append(f"{where}.{path}.{cid}: no {_LABEL[kind]} for node {node}")

        known_probes = rules.checks.url_probe.degraded_after_seconds
        used_probes = {ref.partition(":")[2] for comp in enabled.values() for ref in comp.checks
                       if ref.startswith("url_probe:")}
        for probe in sorted(used_probes):
            if probe not in env.url_probes.probes:
                problems.append(f"{where}.url_probes.probes: no target for probe {probe}")
        for probe, target in env.url_probes.probes.items():
            if probe not in known_probes:
                problems.append(f"{where}.url_probes.probes.{probe}: not a probe in rules.yaml "
                                f"{sorted(known_probes)}")
            if target.port not in env.ports:
                problems.append(f"{where}.url_probes.probes.{probe}.port: {target.port!r} is not a key "
                                f"in ports")

        if env.sql.port not in env.ports:
            problems.append(f"{where}.sql.port: {env.sql.port!r} is not a key in ports")
        if env.sql.auth == "sql" and "sql_monitor_login" not in env.secrets:
            problems.append(f"{where}.sql.auth: 'sql' needs secrets.sql_monitor_login")

        for node in env.nodes:
            if node not in used_nodes:
                problems.append(f"{where}.nodes.{node}: not used by any enabled component in rules.yaml")

        for kind in NAMED_CHECKS:
            path, table = env.names_for(kind)
            for cid, names in table.items():
                if cid not in rules.components:
                    problems.append(f"{where}.{path}.{cid}: no such component in rules.yaml")
                    continue
                if kind not in rules.components[cid].checks:
                    problems.append(f"{where}.{path}.{cid}: component {cid} has no {kind} check in "
                                    f"rules.yaml")
                for node in names:
                    if node not in env.nodes:
                        problems.append(f"{where}.{path}.{cid}.{node}: node not declared in this environment")
                    elif node not in rules.components[cid].nodes:
                        problems.append(f"{where}.{path}.{cid}.{node}: component {cid} does not run on "
                                        f"{node} in rules.yaml")
    return problems


_LABEL = {"windows_service": "service name", "weblogic_server": "WebLogic server name"}
