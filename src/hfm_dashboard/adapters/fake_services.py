"""In-memory fake of the Windows services port, for tests and offline dashboard runs."""

from __future__ import annotations

from collections.abc import Sequence

from hfm_dashboard.ports.windows_services import NOT_FOUND, ServiceQueryError


class FakeWindowsServiceSource:
    """``states[host][service] = state``; ``failures[host] = reason`` makes that host fail.

    A service not listed for a host is reported ``NotFound``, like the real adapter.
    Every call is recorded in ``calls`` as ``(host, tuple(service_names))``.
    """

    def __init__(
        self,
        states: dict[str, dict[str, str]] | None = None,
        failures: dict[str, str] | None = None,
    ) -> None:
        self.states = states or {}
        self.failures = failures or {}
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def query(self, host: str, service_names: Sequence[str]) -> dict[str, str]:
        self.calls.append((host, tuple(service_names)))
        if host in self.failures:
            raise ServiceQueryError(self.failures[host])
        known = self.states.get(host, {})
        return {name: known.get(name, NOT_FOUND) for name in service_names}
