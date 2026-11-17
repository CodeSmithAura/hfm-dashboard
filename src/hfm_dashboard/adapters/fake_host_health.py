"""In-memory fake of the host-health port, for tests and offline runs."""

from __future__ import annotations

from collections.abc import Sequence

from hfm_dashboard.ports.host_health import HostHealth, HostQueryError

HEALTHY_VOLUMES = {"C:": 45.0, "D:": 60.0}


class FakeHostHealthSource:
    """``volumes[host]``, ``crashes[host][service]``, ``shutdowns[host]``; ``failures[host]`` = reason.
    Hosts without volumes get ``HEALTHY_VOLUMES``."""

    def __init__(self, volumes=None, crashes=None, shutdowns=None, failures=None) -> None:
        self.volumes: dict[str, dict[str, float]] = dict(volumes or {})
        self.crashes: dict[str, dict[str, int]] = dict(crashes or {})
        self.shutdowns: dict[str, int] = dict(shutdowns or {})
        self.failures: dict[str, str] = dict(failures or {})
        self.calls: list[tuple] = []

    def query(self, host: str, service_names: Sequence[str], lookback_minutes: int,
              crash_event_ids: Sequence[int], shutdown_event_ids: Sequence[int]) -> HostHealth:
        self.calls.append((host, tuple(service_names), lookback_minutes, tuple(crash_event_ids),
                           tuple(shutdown_event_ids)))
        if host in self.failures:
            raise HostQueryError(self.failures[host])
        crashes = self.crashes.get(host, {})
        return HostHealth(dict(self.volumes.get(host, HEALTHY_VOLUMES)),
                          {n: crashes.get(n, 0) for n in service_names}, self.shutdowns.get(host, 0))
