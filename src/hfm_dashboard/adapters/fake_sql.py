"""In-memory fake of the SQL Server health port, for tests and offline dashboard runs."""

from __future__ import annotations

from hfm_dashboard.domain.evaluation import SqlServerObservation
from hfm_dashboard.ports.sql_health import SqlQueryError


class FakeSqlHealthSource:
    def __init__(self, observations: dict[str, SqlServerObservation] | None = None,
                 failures: dict[str, str] | None = None) -> None:
        self.observations = dict(observations or {})
        self.failures = dict(failures or {})
        self.calls: list[str] = []

    def fetch(self, host: str) -> SqlServerObservation:
        self.calls.append(host)
        if host in self.failures:
            raise SqlQueryError(self.failures[host])
        return self.observations[host]
