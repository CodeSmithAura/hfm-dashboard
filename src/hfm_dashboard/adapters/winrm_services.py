"""Windows services over WinRM (PowerShell remoting via pypsrp). Read-only: ``Get-Service`` only.

Transport, authentication and error texts: ``winrm_runner.WinRMRunner``.

A service is matched by Name first, then by DisplayName (both case-insensitive), because some
names in environments.yaml were recorded from the DisplayName column during discovery.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from hfm_dashboard.adapters.winrm_runner import Credential, WinRMRunError, WinRMRunner
from hfm_dashboard.adapters.winrm_runner import default_client_factory as _default_client_factory
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


class WinRMServiceSource:
    def __init__(
        self,
        settings: WinRM,
        timeout_seconds: int,
        credential: Credential | None = None,
        client_factory: Callable[..., Any] = _default_client_factory,
        on_output: Callable[[str, str], None] | None = None,
    ) -> None:
        self._runner = WinRMRunner(settings, timeout_seconds, credential, client_factory, on_output)

    def _client_kwargs(self) -> dict[str, Any]:
        return self._runner.client_kwargs()

    def query(self, host: str, service_names: Sequence[str]) -> dict[str, str]:
        if not service_names:
            return {}
        try:
            output = self._runner.run(host, build_script(service_names))
        except WinRMRunError as exc:
            reason = str(exc).replace("query reported", "service query reported")
            raise ServiceQueryError(reason) from exc
        return parse_output(service_names, output)
