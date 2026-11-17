"""Sources that always fail with a fixed reason, used when a collector cannot be set up
(for example its credential is not in the secret store yet). Its checks then show Unknown with
that reason instead of stopping the whole service."""

from __future__ import annotations

from hfm_dashboard.domain.evaluation import SqlServerObservation, WeblogicObservation
from hfm_dashboard.ports.sql_health import SqlQueryError
from hfm_dashboard.ports.weblogic import WeblogicQueryError


class UnavailableWeblogicSource:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def fetch_servers(self) -> dict[str, WeblogicObservation]:
        raise WeblogicQueryError(self.reason)


class UnavailableSqlSource:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def fetch(self, host: str) -> SqlServerObservation:
        raise SqlQueryError(self.reason)
