"""WebLogic collector: one domain-wide query, results evaluated against rules.yaml.

For every enabled component with a ``weblogic_server`` check, each of its nodes in the environment
is matched to its WebLogic server name from environments.yaml. If the Admin Server cannot be
queried, every WebLogic check is Unknown (the windows_service checks still show whether the
servers' Windows services run). A server name the domain does not know is Unknown too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.evaluation import CollectionFailure, evaluate
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.status import CheckResult
from hfm_dashboard.ports.weblogic import WeblogicQueryError, WeblogicSource

CHECK = "weblogic_server"


@dataclass(frozen=True, slots=True)
class WeblogicTarget:
    component: str
    node: str
    server: str


def plan(env: Environment, rules: Rules) -> list[WeblogicTarget]:
    targets = []
    for cid, comp in rules.enabled_components().items():
        if CHECK not in comp.checks:
            continue
        for node in env.nodes_of(cid, rules):
            targets.append(WeblogicTarget(cid, node, env.weblogic_server(cid, node)))
    return targets


def collect_weblogic(env: Environment, rules: Rules, source: WeblogicSource,
                     observed_at: datetime) -> list[CheckResult]:
    targets = plan(env, rules)
    if not targets:
        return []
    try:
        servers = source.fetch_servers()
        failure = None
    except WeblogicQueryError as exc:
        servers, failure = {}, str(exc)

    results = []
    for t in targets:
        if failure is not None:
            observation = CollectionFailure(failure)
        elif t.server not in servers:
            observation = CollectionFailure("server not found in the WebLogic domain")
        else:
            observation = servers[t.server]
        ev = evaluate(CHECK, observation, rules.checks)
        results.append(CheckResult(
            component=t.component, node=t.node, check=CHECK, status=ev.status,
            observed_at=observed_at, detail=f"{t.server}: {ev.detail}",
        ))
    return results
