"""Port: read the state of Windows services on one host (read-only)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

NOT_FOUND = "NotFound"


class ServiceQueryError(Exception):
    """The host could not be queried (unreachable, access denied, unexpected output).

    The message is shown on the dashboard: it must never contain a secret or connection string.
    """


class WindowsServiceSource(Protocol):
    def query(self, host: str, service_names: Sequence[str]) -> dict[str, str]:
        """Return ``{requested name: state}`` for every requested name.

        States use the Windows ServiceControllerStatus names (``Running``, ``Stopped``,
        ``StartPending``, ...) or ``NotFound`` when no service matches the name.
        Raises ``ServiceQueryError`` when the host cannot be queried at all.
        """
        ...
