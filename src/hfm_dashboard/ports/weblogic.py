"""Port: read WebLogic server runtime data for the whole domain (read-only)."""

from __future__ import annotations

from typing import Protocol

from hfm_dashboard.domain.evaluation import WeblogicObservation


class WeblogicQueryError(Exception):
    """The domain could not be queried (Admin Server unreachable, access denied, bad output).

    The message is shown on the dashboard: never a secret, URL with credentials or raw response.
    """


class WeblogicSource(Protocol):
    def fetch_servers(self) -> dict[str, WeblogicObservation]:
        """Return ``{server name: observation}`` for every server known to the domain.

        Servers that are not running have their lifecycle state but no metrics or health.
        Raises ``WeblogicQueryError`` when the domain cannot be queried at all.
        """
        ...
