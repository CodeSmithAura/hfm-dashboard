"""Build the real collectors for one environment from configuration and the secret store.

A credential that is not available yet (WebLogic Monitor user A09, SQL login A10) does not stop the
service: that collector is replaced by one that reports Unknown with the reason. Secret values are
fetched here once at start-up and handed to the adapters; they are never logged.
"""

from __future__ import annotations

from hfm_dashboard.adapters.http_probe import HttpxUrlProber
from hfm_dashboard.adapters.sql_health import OdbcSqlHealthSource
from hfm_dashboard.adapters.unavailable import UnavailableSqlSource, UnavailableWeblogicSource
from hfm_dashboard.adapters.weblogic_rest import WeblogicRestSource
from hfm_dashboard.adapters.winrm_services import WinRMServiceSource
from hfm_dashboard.app.poller import Collectors
from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.ports.secrets import Secret, SecretNotFound, SecretStore

WEBLOGIC_SECRET = "weblogic_monitor_user"
SQL_SECRET = "sql_monitor_login"


def _secret(env: Environment, logical: str, store: SecretStore) -> Secret | str:
    """The secret, or a reason string when it cannot be had."""
    name = env.secrets.get(logical)
    if name is None:
        return f"no secrets.{logical} entry in environments.yaml"
    try:
        return store.get(name)
    except SecretNotFound as exc:
        return f"credentials not available: {exc}"


def build_collectors(env: Environment, rules: Rules, store: SecretStore) -> Collectors:
    checks = rules.checks

    wls = _secret(env, WEBLOGIC_SECRET, store)
    weblogic = (UnavailableWeblogicSource(f"WebLogic {wls}") if isinstance(wls, str) else
                WeblogicRestSource(env.weblogic.admin_url, wls, checks.weblogic_server.timeout_seconds,
                                   cert_validation=env.weblogic.cert_validation))

    sql_cred: Secret | None = None
    sql_unavailable: str | None = None
    if env.sql.auth == "sql":
        got = _secret(env, SQL_SECRET, store)
        if isinstance(got, str):
            sql_unavailable = f"SQL Server {got}"
        else:
            sql_cred = got
    sql = (UnavailableSqlSource(sql_unavailable) if sql_unavailable else
           OdbcSqlHealthSource(env.sql, env.ports[env.sql.port], env.databases,
                               checks.sql_server.timeout_seconds,
                               agent_lookback_hours=checks.sql_server.degraded_if_agent_job_failed_in_last_hours,
                               credential=sql_cred))

    return Collectors(
        services=WinRMServiceSource(env.winrm, checks.windows_service.timeout_seconds),
        weblogic=weblogic,
        prober=HttpxUrlProber(checks.url_probe.timeout_seconds, cert_validation=env.url_probes.cert_validation),
        sql=sql,
    )
