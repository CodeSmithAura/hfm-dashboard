"""Windows services collector: one query per host, results evaluated against rules.yaml.

For every enabled component with a ``windows_service`` check, each of its nodes in the environment
is checked using that node's own service name from environments.yaml (never derived). Hosts are
queried in parallel so one slow host does not delay the others; a host that cannot be queried
gives Unknown for every service on it (scoring then follows ``scoring.unknown_nodes``).
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.evaluation import CollectionFailure, ServiceObservation, evaluate
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.status import CheckResult
from hfm_dashboard.ports.windows_services import ServiceQueryError, WindowsServiceSource

CHECK = "windows_service"


@dataclass(frozen=True, slots=True)
class ServiceTarget:
    component: str
    node: str
    host: str
    service: str


def plan(env: Environment, rules: Rules) -> list[ServiceTarget]:
    """Every (component, node, service) to check in this environment, in rules.yaml order."""
    targets = []
    for cid, comp in rules.enabled_components().items():
        if CHECK not in comp.checks:
            continue
        for node in env.nodes_of(cid, rules):
            targets.append(ServiceTarget(cid, node, env.nodes[node].host, env.service_name(cid, node)))
    return targets


def collect_windows_services(
    env: Environment,
    rules: Rules,
    source: WindowsServiceSource,
    observed_at: datetime,
    max_workers: int = 8,
) -> list[CheckResult]:
    targets = plan(env, rules)
    by_host: dict[str, list[str]] = defaultdict(list)
    for t in targets:
        if t.service not in by_host[t.host]:
            by_host[t.host].append(t.service)

    def query(host: str) -> dict[str, str] | ServiceQueryError:
        try:
            return source.query(host, by_host[host])
        except ServiceQueryError as exc:
            return exc

    if by_host:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(by_host))) as pool:
            answers = dict(zip(by_host, pool.map(query, by_host)))
    else:
        answers = {}

    results = []
    for t in targets:
        answer = answers[t.host]
        if isinstance(answer, ServiceQueryError):
            observation = CollectionFailure(str(answer))
        elif t.service not in answer:
            observation = CollectionFailure("no result for this service")
        else:
            observation = ServiceObservation(answer[t.service])
        ev = evaluate(CHECK, observation, rules.checks)
        results.append(CheckResult(
            component=t.component, node=t.node, check=CHECK, status=ev.status,
            observed_at=observed_at, detail=f"{t.service}: {ev.detail}",
        ))
    return results
