"""Disk space and System event log over WinRM. Read-only: Get-CimInstance, Get-Service, Get-WinEvent.

Volumes: local fixed disks (Win32_LogicalDisk DriveType=3). Events: System log entries with the
configured IDs since ``lookback_minutes`` ago (the host's own clock, so its time zone does not
matter). A crash event names the service by display name in its first parameter, so each requested
service is matched by name or display name. Needs the monitoring account in Event Log Readers (A07).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from hfm_dashboard.adapters.winrm_runner import Credential, WinRMRunError, WinRMRunner, default_client_factory
from hfm_dashboard.domain.environment import WinRM
from hfm_dashboard.ports.host_health import HostHealth, HostQueryError

_SCRIPT = """\
$ErrorActionPreference = 'Stop'
$wanted = @({names})
$crashIds = @({crash_ids})
$shutdownIds = @({shutdown_ids})
$since = (Get-Date).AddMinutes(-{minutes})
$vols = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {{
    [pscustomobject]@{{ volume = $_.DeviceID; size = [double]$_.Size; free = [double]$_.FreeSpace }} }})
$ids = @($crashIds + $shutdownIds)
$events = @(Get-WinEvent -FilterHashtable @{{ LogName = 'System'; Id = $ids; StartTime = $since }} -ErrorAction SilentlyContinue)
$svc = @(Get-Service -ErrorAction SilentlyContinue)
$crashes = @(foreach ($w in $wanted) {{
    $s = $svc | Where-Object {{ $_.Name -eq $w -or $_.DisplayName -eq $w }} | Select-Object -First 1
    $names = @($w)
    if ($s) {{ $names += $s.Name; $names += $s.DisplayName }}
    $n = @($events | Where-Object {{ $crashIds -contains $_.Id -and $_.Properties.Count -gt 0 -and $names -contains [string]$_.Properties[0].Value }}).Count
    [pscustomobject]@{{ wanted = $w; crashes = $n }}
}})
$shutdowns = @($events | Where-Object {{ $shutdownIds -contains $_.Id }}).Count
ConvertTo-Json -InputObject ([pscustomobject]@{{ volumes = $vols; crashes = $crashes; shutdowns = $shutdowns }}) -Compress -Depth 4
"""


def _ps_quote(value: str) -> str:
    """PowerShell single-quoted literal: no expansion; embedded quotes doubled."""
    return "'" + value.replace("'", "''") + "'"


def build_script(service_names: Sequence[str], lookback_minutes: int, crash_ids: Sequence[int],
                 shutdown_ids: Sequence[int]) -> str:
    return _SCRIPT.format(
        names=", ".join(_ps_quote(n) for n in service_names),
        crash_ids=", ".join(str(int(i)) for i in crash_ids),
        shutdown_ids=", ".join(str(int(i)) for i in shutdown_ids),
        minutes=int(lookback_minutes),
    )


def parse_output(service_names: Sequence[str], output: str) -> HostHealth:
    """Parse the script's JSON; raise on anything unexpected."""
    try:
        body = json.loads(output)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HostQueryError("unexpected output from host query (not JSON)") from exc
    if not isinstance(body, dict):
        raise HostQueryError("unexpected output from host query (not an object)")

    vols = body.get("volumes") or []
    if isinstance(vols, dict):      # one volume serialised as an object
        vols = [vols]
    volumes: dict[str, float] = {}
    for v in vols:
        if not isinstance(v, dict) or not isinstance(v.get("volume"), str):
            raise HostQueryError("unexpected output from host query (volume)")
        size, free = v.get("size"), v.get("free")
        if isinstance(size, (int, float)) and size > 0 and isinstance(free, (int, float)):
            volumes[v["volume"]] = round(100.0 * free / size, 1)

    rows = body.get("crashes") or []
    if isinstance(rows, dict):
        rows = [rows]
    crashes = {r["wanted"]: int(r.get("crashes") or 0) for r in rows
               if isinstance(r, dict) and isinstance(r.get("wanted"), str)}
    missing = [n for n in service_names if n not in crashes]
    if missing:
        raise HostQueryError(f"host query returned no event result for {len(missing)} service(s)")

    shutdowns = body.get("shutdowns")
    if not isinstance(shutdowns, int) or isinstance(shutdowns, bool):
        raise HostQueryError("unexpected output from host query (shutdowns)")
    return HostHealth(volumes, {n: crashes[n] for n in service_names}, shutdowns)


class WinRMHostHealthSource:
    def __init__(
        self,
        settings: WinRM,
        timeout_seconds: int,
        credential: Credential | None = None,
        client_factory: Callable[..., Any] = default_client_factory,
        on_output: Callable[[str, str], None] | None = None,
    ) -> None:
        self._runner = WinRMRunner(settings, timeout_seconds, credential, client_factory, on_output)

    def query(self, host: str, service_names: Sequence[str], lookback_minutes: int,
              crash_event_ids: Sequence[int], shutdown_event_ids: Sequence[int]) -> HostHealth:
        script = build_script(service_names, lookback_minutes, crash_event_ids, shutdown_event_ids)
        try:
            output = self._runner.run(host, script)
        except WinRMRunError as exc:
            raise HostQueryError(str(exc).replace("query reported", "host query reported")) from exc
        return parse_output(service_names, output)
