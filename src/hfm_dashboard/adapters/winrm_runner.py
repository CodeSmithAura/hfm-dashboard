"""Shared WinRM / PowerShell remoting transport (pypsrp) for the read-only host scripts.

Authentication uses the identity the monitoring service runs as (the read-only gMSA, A07) through
Negotiate/Kerberos, so no password is stored. An explicit credential is accepted for development
testing against non-prod servers only (decision D11). Error texts are fixed per failure type;
exception messages are never shown (they can carry user names or URLs).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hfm_dashboard.domain.environment import WinRM


class WinRMRunError(Exception):
    """The script could not be run on the host (message is secret-free)."""


@dataclass(frozen=True, slots=True)
class Credential:
    """Development testing only (D11). Never logged; repr hides the password."""

    username: str
    password: str

    def __repr__(self) -> str:
        return f"Credential(username={self.username!r}, password=***)"


def default_client_factory(host: str, **kwargs: Any):
    from pypsrp.client import Client  # imported lazily so tests do not need a WinRM stack

    return Client(host, **kwargs)


class WinRMRunner:
    def __init__(
        self,
        settings: WinRM,
        timeout_seconds: int,
        credential: Credential | None = None,
        client_factory: Callable[..., Any] = default_client_factory,
        on_output: Callable[[str, str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._timeout = timeout_seconds
        self._credential = credential
        self._client_factory = client_factory
        self._on_output = on_output   # (host, raw script output) - used to record sample responses

    def client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "port": self._settings.port,
            "ssl": self._settings.use_ssl,
            "auth": self._settings.auth,
            "cert_validation": self._settings.cert_validation,
            "connection_timeout": self._timeout,
            "operation_timeout": self._timeout,
            "read_timeout": self._timeout + 5,   # must exceed operation_timeout (WinRM requirement)
        }
        if self._credential is not None:
            kwargs["username"] = self._credential.username
            kwargs["password"] = self._credential.password
        return kwargs

    def run(self, host: str, script: str) -> str:
        """Run a read-only PowerShell script on ``host`` and return its output."""
        try:
            client = self._client_factory(host, **self.client_kwargs())
            with client:
                output, _streams, had_errors = client.execute_ps(script)
        except Exception as exc:  # noqa: BLE001 - every transport failure means "cannot check"
            raise WinRMRunError(describe(exc)) from exc
        if self._on_output is not None:
            self._on_output(host, output)
        if had_errors:
            raise WinRMRunError("query reported an error on the host")
        return output


def describe(exc: Exception) -> str:
    """Short, secret-free reason. Exception text is not used: it can carry user or URL details."""
    name = type(exc).__name__
    if name == "AuthenticationError":
        return "WinRM authentication failed"
    if name in {"WSManFaultError"}:
        return "WinRM refused the request (access rights?)"
    if "Timeout" in name:
        return "WinRM timed out"
    if name in {"WinRMTransportError", "ConnectionError", "ConnectTimeout", "SSLError"}:
        return f"WinRM not reachable ({name})"
    return f"WinRM query failed ({name})"
