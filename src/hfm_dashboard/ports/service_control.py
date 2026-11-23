"""Port: register and control the monitor's OWN Windows service (installer only).

Never used for HFM services - the monitor is read-only against HFM (hard rule 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

BUILT_IN_PREFIXES = ("NT AUTHORITY\\", "NT SERVICE\\")


def needs_password(account: str | None) -> bool:
    """True for an ordinary user account (domain or local). False for LocalSystem (None), a managed
    service account (gMSA / sMSA, name ends with '$': Windows manages its password) and the built-in
    NT AUTHORITY / NT SERVICE accounts."""
    if account is None:
        return False
    return not (account.endswith("$") or account.upper().startswith(BUILT_IN_PREFIXES))


class ServiceControlError(Exception):
    """A service manager call failed; ``access_denied`` when the caller is not an administrator."""

    def __init__(self, message: str, *, access_denied: bool = False) -> None:
        super().__init__(message)
        self.access_denied = access_denied


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    display_name: str
    description: str
    command: tuple[str, ...]         # executable first, then its arguments
    account: str | None = None       # None = LocalSystem; a gMSA / sMSA name ends with '$' and has no password
    delayed_auto_start: bool = True
    # Only for an ordinary user account (D33). Handed to the service manager, which stores it as an
    # LSA secret; never printed, logged or written to a file (excluded from repr).
    password: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class ServiceInfo:
    state: str                       # running | stopped | start_pending | stop_pending | ...
    command_line: str
    account: str
    start_type: str


class ServiceControl(Protocol):
    def info(self, name: str) -> ServiceInfo | None:
        """None when the service is not installed."""
        ...

    def grant_service_logon(self, account: str) -> None:
        """Give the account 'Log on as a service' on this computer (local policy; a group policy for
        that right replaces the local list when it applies)."""
        ...

    def install(self, spec: ServiceSpec) -> None: ...

    def set_recovery(self, name: str, restart_delays_seconds: list[int], reset_after_seconds: int) -> None: ...

    def start(self, name: str) -> None: ...

    def stop(self, name: str, timeout_seconds: int) -> None:
        """Ask the service to stop and wait until it has (or the timeout passes)."""
        ...

    def remove(self, name: str) -> None: ...
