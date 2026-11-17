"""SQL Server collector: one connection per database node, evaluated against rules.yaml.

If the instance cannot be reached the check is Unknown; a stopped SQL Server service is reported
as Down by the windows_service check on the same node, and the worst check wins (D13).
"""

from __future__ import annotations

from datetime import datetime

from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.evaluation import CollectionFailure, evaluate
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.status import CheckResult
from hfm_dashboard.ports.sql_health import SqlHealthSource, SqlQueryError

CHECK = "sql_server"


def collect_sql_server(env: Environment, rules: Rules, source: SqlHealthSource,
                       observed_at: datetime) -> list[CheckResult]:
    results = []
    for cid, comp in rules.enabled_components().items():
        if CHECK not in comp.checks:
            continue
        for node in env.nodes_of(cid, rules):
            try:
                observation = source.fetch(env.nodes[node].host)
            except SqlQueryError as exc:
                observation = CollectionFailure(str(exc))
            ev = evaluate(CHECK, observation, rules.checks)
            results.append(CheckResult(component=cid, node=node, check=CHECK, status=ev.status,
                                       observed_at=observed_at, detail=ev.detail))
    return results
