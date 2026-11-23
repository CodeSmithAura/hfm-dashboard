"""URL probe collector: per-node probes, plus probes through the load balancer.

Per-node: for every enabled component with a ``url_probe:<name>`` check, each of its nodes in the
environment is probed directly on that node's managed-server port. These results feed scoring.

Load balancer: for every enabled component with ``lb_probe: true`` (the OHS / load balancer
component), each probe name in use is also fetched through ``load_balancer.url``. Those results
use the reserved node key ``LB_NODE``; they are reported but kept out of scoring until Q57 decides
how a load-balancer failure should count (decision D22).

All probes run in parallel. Details name the probe and path, never the host or full URL.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.evaluation import evaluate
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.status import CheckResult
from hfm_dashboard.ports.url_probe import UrlProber

LB_NODE = "load_balancer"


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    component: str
    node: str
    probe: str
    url: str
    path: str

    @property
    def check(self) -> str:
        return f"url_probe:{self.probe}"


@dataclass(frozen=True, slots=True)
class ProbeResults:
    nodes: list[CheckResult] = field(default_factory=list)          # feed scoring
    load_balancer: list[CheckResult] = field(default_factory=list)  # reported only (Q57)


def plan(env: Environment, rules: Rules) -> tuple[list[ProbeTarget], list[ProbeTarget]]:
    node_targets: list[ProbeTarget] = []
    used: list[str] = []
    for cid, comp in rules.enabled_components().items():
        for ref in comp.checks:
            kind, _, probe = ref.partition(":")
            if kind != "url_probe":
                continue
            if probe not in used:
                used.append(probe)
            path = env.url_probes.probes[probe].path
            for node in env.nodes_of(cid, rules):
                node_targets.append(ProbeTarget(cid, node, probe, env.node_probe_url(probe, node), path))

    lb_targets = [
        ProbeTarget(cid, LB_NODE, probe, env.lb_probe_url(probe), env.url_probes.probes[probe].path)
        for cid, comp in rules.enabled_components().items() if comp.lb_probe
        for probe in used
    ] if env.load_balancer is not None else []     # no front door in this environment (D34)
    return node_targets, lb_targets


def collect_url_probes(env: Environment, rules: Rules, prober: UrlProber, observed_at: datetime,
                       max_workers: int = 8) -> ProbeResults:
    node_targets, lb_targets = plan(env, rules)
    targets = node_targets + lb_targets
    if not targets:
        return ProbeResults()

    with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as pool:
        observations = list(pool.map(lambda t: prober.probe(t.url), targets))

    def result(t: ProbeTarget, obs) -> CheckResult:
        ev = evaluate(t.check, obs, rules.checks)
        return CheckResult(component=t.component, node=t.node, check=t.check, status=ev.status,
                           observed_at=observed_at, detail=f"{t.probe} ({t.path}): {ev.detail}")

    results = [result(t, o) for t, o in zip(targets, observations)]
    return ProbeResults(nodes=results[:len(node_targets)], load_balancer=results[len(node_targets):])
