"""Port: read SQL Server health for one instance (read-only queries)."""

from __future__ import annotations

from typing import Protocol

from hfm_dashboard.domain.evaluation import SqlServerObservation


class SqlQueryError(Exception):
    """The instance could not be reached or logged in to. Never contains a connection string."""


class SqlHealthSource(Protocol):
    def fetch(self, host: str) -> SqlServerObservation:
        """Health of the instance on ``host``. A metric whose query fails is ``None`` (Unknown);
        ``SqlQueryError`` only when no connection can be made at all."""
        ...
