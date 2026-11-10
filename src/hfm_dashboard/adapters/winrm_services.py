"""Windows services over WinRM (PowerShell remoting via pypsrp). Read-only: ``Get-Service`` only.

Authentication uses the identity the monitoring service runs as (the read-only gMSA, A07) through
Negotiate/Kerberos, so no password is stored. An explicit credential can be passed for development
testing against non-prod servers (decision D11: never in production).

A service is matched by Name first, then by DisplayName (both case-insensitive), because some
names in environments.yaml were recorded from the DisplayName column during discovery.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from hfm_dashboard.domain.environment import WinRM
from hfm_dashboard.ports.windows_services import NOT_FOUND, ServiceQueryError

_SCRIPT = """\
$ErrorActionPreference = 'Stop'
$wanted = @({names})
$all = @(Get-Service -ErrorAction SilentlyContinue)
$out = foreach ($w in $wanted) {{
    $s = $all | Where-Object {{ $_.Name -eq $w }} | Select-Object -First 1
    if (-not $s) {{ $s = $all | Where-Object {{ $_.DisplayName -eq $w }} | Select-Object -First 1 }}
    if ($s) {{ [pscustomobject]@{{ wanted = $w; status = $s.Status.ToString() }} }}
    else {{ [pscustomobject]@{{ wanted = $w; status = '{not_found}' }} }}
}}
ConvertTo-Json -InputObject @($out) -Compress
"""


def _ps_quote(value: str) -> str:
    """PowerShell single-quoted literal: no expansion; embedded quotes doubled."""
    return "'" + value.replace("'", "''") + "'"


def build_script(service_names: Sequence[str]) -> str:
    return _SCRIPT.format(names=", ".join(_ps_quote(n) for n in service_names), not_found=NOT_FOUND)


def parse_output(service_names: Sequence[str], output: str) -> dict[str, str]:
    """Parse the script's JSON into ``{requested name: state}``; raise on anything unexpected."""
    try:
        rows = json.loads(output)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ServiceQueryError("unexpected output from service query (not JSON)") from exc
    if isinstance(rows, dict):   # defensive: a single object instead of a one-element array
        rows = [rows]
    if not isinstance(rows, list):
        raise ServiceQueryError("unexpected output from service query (not a list)")

    states: dict[str, str] = {}
    for row in rows:
        if not (isinstance(row, dict) and isinstance(row.get("wanted"), str)
                and isinstance(row.get("status"), str)):
            raise ServiceQueryError("unexpected output from service query (bad row)")
        states[row["wanted"]] = row["status"]

    missing = [n for n in service_names if n not in states]
    if missing:
        raise ServiceQueryError(f"service query returned no result for {len(missing)} service(s)")
    return {n: states[n] for n in service_names}


@dataclass(frozen=True, slots=True)
class Credential:
    """Development testing only (D11). Never logged; repr hides the password."""

    username: str
    password: str

    def __repr__(self) -> str:
        return f"Credential(username={self.username!r}, password=***)"


def _default_client_factory(host: str, **kwargs: Any):
    from pypsrp.client import Client  # imported lazily so tests do not need a WinRM stack

    return Client(host, **kwargs)


class WinRMServiceSource:
    def __init__(
        self,
        settings: WinRM,
        timeout_seconds: int,
        credential: Credential | None = None,
        client_factory: Callable[..., Any] = _default_client_factory,
    ) -> None:
        self._settings = settings
        self._timeout = timeout_seconds
        self._credential = credential
        self._client_factory = client_factory

    def _client_kwargs(self) -> dict[str, Any]:
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

    def query(self, host: str, service_names: Sequence[str]) -> dict[str, str]:
        if not service_names:
            return {}
        try:
            client = self._client_factory(host, **self._client_kwargs())
            with client:
                output, streams, had_errors = client.execute_ps(build_script(service_names))
        except ServiceQueryError:
            raise
        except Exception as exc:  # noqa: BLE001 - every transport failure means "cannot check"
            raise ServiceQueryError(_describe(exc)) from exc
        if had_errors:
            raise ServiceQueryError("service query reported an error on the host")
        return parse_output(service_names, output)


def _describe(exc: Exception) -> str:
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
