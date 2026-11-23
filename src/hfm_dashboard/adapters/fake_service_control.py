"""In-memory ServiceControl for tests: records every call, no service manager involved."""

from __future__ import annotations

import subprocess

from hfm_dashboard.ports.service_control import ServiceControlError, ServiceInfo, ServiceSpec


class FakeServiceControl:
    def __init__(self, *, fail_with: ServiceControlError | None = None) -> None:
        self.services: dict[str, ServiceSpec] = {}
        self.states: dict[str, str] = {}
        self.recovery: dict[str, tuple[list[int], int]] = {}
        self.calls: list[tuple[str, str]] = []
        self.logon_rights: set[str] = set()
        self.fail_with = fail_with
        self.fail_grant_with: ServiceControlError | None = None

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def info(self, name: str) -> ServiceInfo | None:
        spec = self.services.get(name)
        if spec is None:
            return None
        return ServiceInfo(state=self.states[name], command_line=subprocess.list2cmdline(spec.command),
                           account=spec.account or "LocalSystem", start_type="auto")

    def grant_service_logon(self, account: str) -> None:
        self.calls.append(("grant_service_logon", account))
        if self.fail_grant_with is not None:
            raise self.fail_grant_with
        self.logon_rights.add(account)

    def install(self, spec: ServiceSpec) -> None:
        self.calls.append(("install", spec.name))
        self._maybe_fail()
        self.services[spec.name] = spec
        self.states[spec.name] = "stopped"

    def set_recovery(self, name: str, restart_delays_seconds: list[int], reset_after_seconds: int) -> None:
        self.calls.append(("set_recovery", name))
        self.recovery[name] = (list(restart_delays_seconds), reset_after_seconds)

    def start(self, name: str) -> None:
        self.calls.append(("start", name))
        self.states[name] = "running"

    def stop(self, name: str, timeout_seconds: int) -> None:
        self.calls.append(("stop", name))
        self.states[name] = "stopped"

    def remove(self, name: str) -> None:
        self.calls.append(("remove", name))
        self.services.pop(name)
        self.states.pop(name)
