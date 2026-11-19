# Installing the monitor as a Windows service (runbook)

For the monitoring server (A06). Real names never go into this repository: replace each
`{{PLACEHOLDER}}` on the server only. The monitor is **read-only** against HFM; this service never
starts, stops or changes an HFM service (hard rule 3).

| Placeholder | Meaning |
|---|---|
| `{{INSTALL_ROOT}}` | Folder for the monitor, e.g. a folder on a data drive |
| `{{MONITOR_GMSA}}` | Read-only gMSA from A07, written `DOMAIN\name$` |
| `{{PYTHON_EXE}}` | Python 3.12 64-bit installed **for all users**, e.g. under `C:\Program Files\Python312\` |

## 1. Prerequisites

- Python 3.12 64-bit, installed for all users (the service runs this interpreter directly, so it must
  not sit in a person's profile folder). ODBC Driver 18 for SQL Server.
- The gMSA is installed on this server and works: `Test-ADServiceAccount <name>` returns `True`
  (infrastructure team, A07).
- The gMSA has **Log on as a service** on this server (group policy or local security policy), WinRM
  read access and Event Log Readers on every HFM server (A07, A08), the WebLogic Monitor role (A09) and
  SQL Server VIEW SERVER STATE + msdb read (A10).
- The dashboard port is open from the admin machines only (no HTTPS / login yet, slice 15).

## 2. Folder layout

```
{{INSTALL_ROOT}}\app      code and its .venv (gMSA: read + execute)
{{INSTALL_ROOT}}\config   rules.yaml, environments.yaml, owners.yaml (gMSA: read; admins: write)
{{INSTALL_ROOT}}\data     history.sqlite (gMSA: modify)
{{INSTALL_ROOT}}\logs     hfm-dashboard.log, one file per UTC day (gMSA: modify)
```

## 3. Install the code (elevated PowerShell)

```powershell
cd {{INSTALL_ROOT}}\app                      # copy of the repository (release tag)
& "{{PYTHON_EXE}}" -m venv .venv
.venv\Scripts\python -m pip install .
```

## 4. Configuration

- `config\rules.yaml` from the same release as the code (new keys are required at start-up).
- `config\environments.yaml` from `environments.template.yaml`, every placeholder filled.
- `config\owners.yaml` from `owners.template.yaml` (Q32). **Shadow mode:** list only the monitoring
  team as owners and default, so no component owner is emailed until go-live.
- `config\maintenance.yaml` from `maintenance.template.yaml` when planned work is announced (delete the
  example windows). Optional; edits apply on the next poll without a restart - check the log afterwards.

## 5. Permissions

```powershell
$acct = '{{MONITOR_GMSA}}'
icacls {{INSTALL_ROOT}}\app    /grant "${acct}:(OI)(CI)RX"
icacls {{INSTALL_ROOT}}\config /grant "${acct}:(OI)(CI)R"
icacls {{INSTALL_ROOT}}\data   /grant "${acct}:(OI)(CI)M"
icacls {{INSTALL_ROOT}}\logs   /grant "${acct}:(OI)(CI)M"
```

The Python installation folder is readable by all users by default; check if it was locked down.

## 6. Secrets (interim until Q55)

WinRM and SQL Server use the gMSA itself, so no password is needed for them. The WebLogic Monitor user
is read from environment variables named after `secrets:` in `environments.yaml`, e.g.
`HFM_MON_PROD_WEBLOGIC_USERNAME` / `_PASSWORD`. Give them to the service only (not machine-wide), after
step 7:

```powershell
$key  = 'HKLM:\SYSTEM\CurrentControlSet\Services\HfmAvailabilityMonitor'
$user = Read-Host 'WebLogic monitor user'
$pw   = Read-Host 'WebLogic monitor password' -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw))
New-ItemProperty -Path $key -Name Environment -PropertyType MultiString -Force -Value @(
  "HFM_MON_PROD_WEBLOGIC_USERNAME=$user", "HFM_MON_PROD_WEBLOGIC_PASSWORD=$plain")
Remove-Variable plain, pw
```

Limitation: the value is stored in the registry in plain text, readable by local users. Acceptable
for shadow mode on a server only admins can log on to; Q55 (secret store) must be decided before
go-live.

## 7. Check, install, start (elevated PowerShell)

```powershell
cd {{INSTALL_ROOT}}\app
$opts = '--config-dir', '{{INSTALL_ROOT}}\config', '--data-dir', '{{INSTALL_ROOT}}\data', '--log-dir', '{{INSTALL_ROOT}}\logs'
.venv\Scripts\hfm-service check @opts                       # exact service command line, --check mode
.venv\Scripts\hfm-service install @opts --account '{{MONITOR_GMSA}}'
# (step 6 now, if WebLogic credentials are needed)
Start-Service HfmAvailabilityMonitor
```

`install` refuses if the configuration is invalid or the self-check fails. It registers automatic
(delayed) start, Windows recovery actions from `rules.yaml monitor_service` (restart after 60 / 120 /
300 s) and the event log source. The self-check runs as **you**, not as the gMSA: folder rights and
remote access of the gMSA show up only once the service runs (step 8).

## 8. Verify

```powershell
.venv\Scripts\hfm-service status                            # running, account, command line
Get-Content {{INSTALL_ROOT}}\logs\hfm-dashboard.log -Tail 30 # poll summaries every poll
sc.exe qfailure HfmAvailabilityMonitor                      # recovery actions
Invoke-WebRequest http://localhost:{{DASHBOARD_PORT}}/healthz -UseBasicParsing   # if bound to localhost
```

Event Viewer > Windows Logs > Application, source `HfmAvailabilityMonitor`: start, stop, refusals
and crashes. Expect every check Unknown while an access item (A07-A10) is still missing.

## 9. Upgrade

```powershell
Stop-Service HfmAvailabilityMonitor
# replace {{INSTALL_ROOT}}\app with the new release, then: .venv\Scripts\python -m pip install .
# update config\rules.yaml from the same release
.venv\Scripts\hfm-service check @opts
Start-Service HfmAvailabilityMonitor
```

Uninstall and install again if the Python installation, the folders or the recovery values in
rules.yaml change (they are fixed at install time). The Environment value from step 6 is removed with
the service and must be set again.

## 10. Uninstall

```powershell
.venv\Scripts\hfm-service uninstall      # stops (waits up to stop_timeout_seconds), removes; data and logs kept
```

## Troubleshooting

| Symptom | Meaning / action |
|---|---|
| Starts, then stops within seconds; event "start-up refused" | Configuration problem. Reason in the log; fix and start again (not restarted automatically) |
| Event 7031 / 7034, service restarts | Crash; traceback in the log. Recovery restarts it (60 / 120 / 300 s) |
| Log: "dashboard could not start" | Port in use or bind address not on this server (`monitoring` in environments.yaml); treated as a crash and retried |
| Error 1069 at start | gMSA logon failed: Log on as a service right, or `Test-ADServiceAccount` fails |
| Error 1053 at start | Interpreter did not start: Python moved or removed, or not readable by the gMSA; run `hfm-service check` |
| Log: "maintenance.yaml changed but is invalid" | The edit was rejected and the previous windows still apply; fix the file (reason in the log) |
| Every check Unknown | Monitoring access missing (A07-A10) or WebLogic secret not set (step 6); reasons in the log |
