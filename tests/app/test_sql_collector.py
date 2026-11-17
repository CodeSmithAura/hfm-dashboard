"""SQL Server collector against the filled template and the shipped rules."""

from __future__ import annotations

import json

import pytest

from hfm_dashboard.adapters.fake_sql import FakeSqlHealthSource
from hfm_dashboard.adapters.sql_health import OdbcSqlHealthSource, build_observation
from hfm_dashboard.app.sql_collector import collect_sql_server
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.status import Status
from tests.adapters.test_sql_health import Connector
from tests.conftest import FIXTURES, FILLED, T0

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN
DB1 = "db-node-01.example.test"


@pytest.fixture(scope="module")
def prod(repo_rules):
    return parse_environments(FILLED, repo_rules).environments["PROD"]


def sample(name):
    return json.loads((FIXTURES / "sql" / name).read_text(encoding="utf-8"))


def test_healthy(prod, repo_rules):
    src = FakeSqlHealthSource({DB1: build_observation(sample("healthy.json"), prod.databases)})
    (r,) = collect_sql_server(prod, repo_rules, src, T0)
    assert (r.component, r.node, r.check, r.status) == ("sql_server", "db_node_01", "sql_server", U)
    assert r.observed_at == T0 and src.calls == [DB1]


def test_unreachable_is_unknown(prod, repo_rules):
    src = FakeSqlHealthSource(failures={DB1: "SQL Server not reachable"})
    (r,) = collect_sql_server(prod, repo_rules, src, T0)
    assert r.status is X and "SQL Server not reachable" in r.detail


def test_end_to_end_with_odbc_adapter(prod, repo_rules):
    """Real adapter (fake ODBC connection) + collector + shipped rules + environment settings."""
    rule = repo_rules.checks.sql_server
    src = OdbcSqlHealthSource(prod.sql, prod.ports[prod.sql.port], prod.databases, rule.timeout_seconds,
                              agent_lookback_hours=rule.degraded_if_agent_job_failed_in_last_hours,
                              connect=Connector(sample("production_disks.json")))
    (r,) = collect_sql_server(prod, repo_rules, src, T0)
    assert r.status is G and r.detail == "C: 8.6% free < 10%"
