"""In-memory fake of the WebLogic port, for tests and offline dashboard runs."""

from __future__ import annotations

from hfm_dashboard.domain.evaluation import WeblogicObservation
from hfm_dashboard.ports.weblogic import WeblogicQueryError


class FakeWeblogicSource:
    def __init__(self, servers: dict[str, WeblogicObservation] | None = None,
                 failure: str | None = None) -> None:
        self.servers = dict(servers or {})
        self.failure = failure
        self.calls = 0

    def fetch_servers(self) -> dict[str, WeblogicObservation]:
        self.calls += 1
        if self.failure:
            raise WeblogicQueryError(self.failure)
        return dict(self.servers)
