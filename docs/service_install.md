# Installing the monitor as a Windows service (runbook)

For the monitoring server (A06). Real names never go into this repository: replace each
`{{PLACEHOLDER}}` on the server only. The monitor is **read-only** against HFM; this service never
starts, stops or changes an HFM service (hard rule 3).

| Placeholder | Meaning |
|---|---|
| `{{INSTALL_ROOT}}` | Folder for the monitor that you choose, e.g. `D:\HfmMonitor` (not under a user profile; a data drive is best) |
| `{{MONITOR_ACCOUNT}}` | The account the service runs as - see "Which account" below |
| `{{PYTHON_EXE}}` | Python 3.12 64-bit installed **for all users**, e.g. under `C:\Program Files\Python312\` |

## Which account

| Account | Written | Password | Status |
|---|---|---|---|
| gMSA / sMSA (managed service account) | `DOMAIN\name$` | none - Windows manages it | Planned (A07) |
| Ordinary domain user used as a service account | `DOMAIN\name` | given at install (`--ask-password`), kept by Windows | **Interim** (D33), while the managed account is awaited |

Find out which one you have (AD PowerShell module): `Get-ADServiceAccount <name>` finds only managed
accounts; `Get-ADUser <name>` showing `ObjectClass user` means an ordinary user account. For an
ordinary user skip `Install-ADServiceAccount` / `Test-ADServiceAccount` - they apply to managed accounts only.

## 1. Prerequisites

- Python 3.12 64-bit, installed **for all users** (installer > Customize installation > "Install
  Python 3.12 for all users", location `C:\Program Files\Python312\`). The service runs this
  interpreter directly as its own account, which cannot read a Python installed "for me only" in your
  profile; `hfm-service check` refuses that (D36). Check: `& "{{PYTHON_EXE}}" -c "import sys;
  print(sys.base_prefix)"` must not be under `C:\Users`. ODBC Driver 18 for SQL Server.
- Run every step below signed in with a **domain** account that is a local administrator on this
  server (`whoami` shows `DOMAIN\you`, not `SERVER\...`), in an elevated PowerShell.
- Managed account only: it is installed on this server and works: `Test-ADServiceAccount <name>`
  returns `True` (infrastructure team, A07).
- The service account has WinRM read access and Event Log Readers on every monitored HFM server (A07,
  A08), the WebLogic Monitor role (A09) and SQL Server VIEW SERVER STATE + msdb read (A10). Missing
  rights do not stop the service: those checks show Unknown with the reason.
- **Log on as a service** on this server: `hfm-service install` grants it locally. If a group policy
  sets that right, the policy replaces the local list - then the account must be added to the policy.
- The dashboard port is open from the admin machines only (no HTTPS / login yet, slice 15).

## 2. Folder layout

```
{{INSTALL_ROOT}}\app      code and its .venv (service account: read + execute)
{{INSTALL_ROOT}}\config   rules.yaml, environments.yaml, owners.yaml (service account: read; admins: write)
{{INSTALL_ROOT}}\data     history.sqlite (service account: modify)
{{INSTALL_ROOT}}\logs     hfm-dashboard.log, one file per UTC day (service account: modify)
{{INSTALL_ROOT}}\wheelhouse  offline packages, only if the server has no internet (step 3b)
```

## 3. Install the code (elevated PowerShell)

Copy the whole repository (release tag) into `{{INSTALL_ROOT}}\app` - `pip` needs `pyproject.toml`
next to `src`. Never copy a `.venv` from another machine; create it on the server. The service runs
the copy that `pip` puts into `.venv`, so after any code change install again.

Check first: `& "{{PYTHON_EXE}}" -V` must show Python 3.12, 64-bit (several packages are built for
exactly that version).

### 3a. Server with internet access to PyPI

```powershell
cd {{INSTALL_ROOT}}\app
& "{{PYTHON_EXE}}" -m venv .venv
.venv\Scripts\python -m pip install .
```

If this fails with an SSL error such as `UNEXPECTED_EOF_WHILE_READING` or "could not fetch URL
https://pypi.org/...", the server cannot reach PyPI (firewall or proxy): use 3b. Nothing was changed
by the failed attempt; the same `.venv` can be used.

### 3b. Server without internet access (offline wheelhouse)

On a build machine **with** internet, Windows, Python 3.12 64-bit, in a checkout of the same release
(with its own venv):

```powershell
cd <repository>
.venv\Scripts\python -m pip wheel . -w <folder>\wheelhouse      # the monitor + every dependency, ~17 MB
Compress-Archive <folder>\wheelhouse <folder>\wheelhouse.zip -Force
```

Copy `wheelhouse.zip` to the server and unzip it as `{{INSTALL_ROOT}}\wheelhouse`. Then on the server:

```powershell
cd {{INSTALL_ROOT}}\app
& "{{PYTHON_EXE}}" -m venv .venv
.venv\Scripts\python -m pip install --no-index --find-links {{INSTALL_ROOT}}\wheelhouse hfm-dashboard
.venv\Scripts\hfm-service --help                                  # installed?
```

`--no-index` keeps pip off the internet entirely. The code then comes from the wheelhouse, not from
`app\src`; `app` is still the source of `config\*.yaml` templates and `rules.yaml`.

## 4. Configuration

- `config\rules.yaml` from the same release as the code (new keys are required at start-up).
- `config\environments.yaml` from `environments.template.yaml`, every placeholder filled. Only the
  environments you can reach need to be in it - see "Interim: NONPROD only" below.
- `config\owners.yaml` from `owners.template.yaml` (Q32). **Shadow mode:** list only the monitoring
  team as owners and default, so no component owner is emailed until go-live.
- `config\maintenance.yaml` from `maintenance.template.yaml` when planned work is announced (delete the
  example windows). Optional; edits apply on the next poll without a restart - check the log afterwards.

## 5. Permissions

```powershell
$acct = '{{MONITOR_ACCOUNT}}'
icacls {{INSTALL_ROOT}}\app    /grant "${acct}:(OI)(CI)RX"
icacls {{INSTALL_ROOT}}\config /grant "${acct}:(OI)(CI)R"
icacls {{INSTALL_ROOT}}\data   /grant "${acct}:(OI)(CI)M"
icacls {{INSTALL_ROOT}}\logs   /grant "${acct}:(OI)(CI)M"
```

The Python installation folder is readable by all users by default; check if it was locked down.

## 6. Secrets (interim until Q55)

WinRM uses the service account itself (no password stored). Credentials for WebLogic, and for SQL
Server when it uses a SQL login (`sql.auth: sql`), are read from environment variables named after
the `secrets:` entries in `environments.yaml`: every non-alphanumeric character becomes `_`, upper
case, plus `_USERNAME` / `_PASSWORD`:

| Secret (environments.yaml) | Variables |
|---|---|
| `weblogic_monitor_user: "hfm-mon/NONPROD/weblogic"` | `HFM_MON_NONPROD_WEBLOGIC_USERNAME`, `HFM_MON_NONPROD_WEBLOGIC_PASSWORD` |
| `sql_monitor_login: "hfm-mon/NONPROD/sql"` (only with `sql.auth: sql`) | `HFM_MON_NONPROD_SQL_USERNAME`, `HFM_MON_NONPROD_SQL_PASSWORD` |

Give them to the service only (not machine-wide), after step 7, in the service's registry
`Environment` value. Use PowerShell ISE or a normal window **as administrator**; `Read-Host
-AsSecureString` opens a dialog in ISE, and paste works there. This script **adds or replaces one
secret and keeps the others** - run it once per secret; set `$prefix` first:

```powershell
$prefix = 'HFM_MON_NONPROD_SQL'          # or HFM_MON_NONPROD_WEBLOGIC, HFM_MON_PROD_WEBLOGIC, ...
$key  = 'HKLM:\SYSTEM\CurrentControlSet\Services\HfmAvailabilityMonitor'
$user = Read-Host "$prefix user"
$pw   = Read-Host "$prefix password" -AsSecureString
$plain = [Net.NetworkCredential]::new('', $pw).Password
# @( ... ) around the whole pipeline: without it an empty result turns the next line into one glued string
$keep = @((Get-ItemProperty $key -Name Environment -ErrorAction SilentlyContinue).Environment |
          Where-Object { $_ -and $_ -notlike "${prefix}_*" })
$values = $keep + @("${prefix}_USERNAME=$user", "${prefix}_PASSWORD=$plain")
New-ItemProperty -Path $key -Name Environment -PropertyType MultiString -Force -Value $values
Remove-Variable plain, pw, values

# check - names only, never the values: each secret must show as TWO lines (_USERNAME and _PASSWORD)
(Get-ItemProperty $key -Name Environment).Environment | ForEach-Object { ($_ -split '=', 2)[0] }
Restart-Service HfmAvailabilityMonitor   # read at service start; no server restart needed
```

Limitation: the value is stored in the registry in plain text, readable by local users. Acceptable
for shadow mode on a server only admins can log on to; Q55 (secret store) must be decided before
go-live. The value is removed with the service (uninstall) and must be set again after a reinstall.
Without the WebLogic value the service still runs: the WebLogic check (Admin Server, Foundation /
Shared Services) shows Unknown, those nodes drop out of the score, and a stopped Windows service is
still detected.

**SQL Server certificate.** ODBC Driver 18 encrypts and verifies the SQL Server's certificate
(`encrypt: true`, `trust_server_certificate: false`). A self-signed certificate - usual on
non-production - fails with "SQL Server not reachable" (ODBC: "The certificate chain was issued by an
authority that is not trusted"). NONPROD interim: `trust_server_certificate: true` (still encrypted,
the server's identity is not checked). PROD: keep `false` and have the issuing root certificate
trusted on the monitoring server, with the host name in `environments.yaml` matching the
certificate (Q65).

## 7. Check, install, start (elevated PowerShell)

```powershell
cd {{INSTALL_ROOT}}\app
$opts = '--config-dir', '{{INSTALL_ROOT}}\config', '--data-dir', '{{INSTALL_ROOT}}\data', '--log-dir', '{{INSTALL_ROOT}}\logs'
.venv\Scripts\hfm-service check @opts                       # exact service command line, --check mode

# managed account (gMSA / sMSA):
.venv\Scripts\hfm-service install @opts --account 'DOMAIN\name$'
# OR interim ordinary user account (D33) - prompts twice for its password, nothing is echoed or saved in a file:
.venv\Scripts\hfm-service install @opts --account 'DOMAIN\name' --ask-password

# (step 6 now, if credentials are available)
Start-Service HfmAvailabilityMonitor
```

`check` and `install` refuse if the configuration is invalid, if the Python interpreter or a folder
lies inside a user profile (D36 - e.g. Python installed "for me only"; the service account cannot read
it), or if the self-check fails. `install` also refuses an ordinary user account without
`--ask-password` (it could not log on). It grants the account **Log on as a service** (before
anything is registered, so a mistyped account installs nothing), registers automatic (delayed) start,
Windows recovery actions from `rules.yaml monitor_service` (restart after 60 / 120 / 300 s) and the
event log source. The password is asked for only after the checks passed and goes straight to
Windows (stored like any service password, as an LSA secret). The self-check runs as **you**, not as
the service account: folder rights and remote access of the service account show up only once the
service runs (step 8). For a test on your own workstation only, `--allow-user-profile` skips the
profile check.

**If the password prompt does not work** (it shows a blank line, or the console takes no typing -
PowerShell ISE, some remote-access tools): install without `--account`, then set the account in
services.msc, where paste works:

1. `.venv\Scripts\hfm-service install @opts` (registers it as LocalSystem; ignore that warning; do not
   start it).
2. services.msc > HFM Availability Monitor > **Log On** > This account `DOMAIN\name` > password twice
   > OK. Windows grants **Log On As A Service** itself.
3. Step 5 (`icacls`) for that account, `Start-Service HfmAvailabilityMonitor`, then
   `hfm-service status` must show the account (not LocalSystem).

## 8. Verify

```powershell
.venv\Scripts\hfm-service status                            # running, account, command line
Get-Content {{INSTALL_ROOT}}\logs\hfm-dashboard.log -Tail 30 # poll summaries every poll
sc.exe qfailure HfmAvailabilityMonitor                      # recovery actions
Invoke-WebRequest http://localhost:{{DASHBOARD_PORT}}/healthz -UseBasicParsing   # if bound to localhost
```

Event Viewer > Windows Logs > Application, source `HfmAvailabilityMonitor`: start, stop, refusals
and crashes. Expect checks Unknown (with the reason) while an access item (A07-A10) is still missing.

## 9. Password change (interim user account only)

Windows does not learn about a changed password by itself. After every change of the service
account's password (expiry, rotation), before the next restart:

1. services.msc > HFM Availability Monitor > **Log On** tab > enter the new password twice > OK.
2. `Restart-Service HfmAvailabilityMonitor` and check the log (step 8).

If this is missed the service keeps running until the next restart, then fails with error 1069.
Ask the client whether the password expires and who changes it (Q64).

## 10. Upgrade

```powershell
Stop-Service HfmAvailabilityMonitor
# replace {{INSTALL_ROOT}}\app with the new release (keep .venv), then install it:
.venv\Scripts\python -m pip install .                                            # 3a, with internet
# or, offline (3b): rebuild the wheelhouse from the new release, replace {{INSTALL_ROOT}}\wheelhouse, then
.venv\Scripts\python -m pip install --no-index --find-links {{INSTALL_ROOT}}\wheelhouse --force-reinstall hfm-dashboard
# update config\rules.yaml from the same release
.venv\Scripts\hfm-service check @opts
Start-Service HfmAvailabilityMonitor
```

Offline, `--force-reinstall` is required when the version number has not changed (`pip show hfm-dashboard`): without
it pip reports "already satisfied" and keeps the old code.

Uninstall and install again if the Python installation, the folders, the account or the recovery
values in rules.yaml change (they are fixed at install time). The Environment value from step 6 is
removed with the service and must be set again.

**Switching from the interim user account to the managed account** (when A07 arrives): uninstall
(step 11), repeat step 5 for the new account, install with `--account 'DOMAIN\name$'` (no
`--ask-password`), step 6, start. History and logs are kept. Then ask the client to remove the user
account's rights.

## 11. Uninstall

```powershell
.venv\Scripts\hfm-service uninstall      # stops (waits up to stop_timeout_seconds), removes; data and logs kept
```

## Interim: NONPROD only (D34)

While production access is not given, the monitor can watch NONPROD alone; nothing in the code
changes when PROD is added later.

- `environments.yaml`: keep only the `NONPROD:` block under `environments:` (delete or leave out
  `PROD:`), plus `monitoring:`. When PROD access arrives, add the filled `PROD:` block and restart.
- `load_balancer` is optional. Development has no front door, so leave it out (as in the template):
  the dashboard shows no load-balancer probes for NONPROD. If users do reach Workspace through an OHS
  URL, add the block with that URL instead.
- NONPROD notices are logged only (`notices_enabled: false`); nothing is emailed.
- The WebLogic secret uses the NONPROD names (step 6). Without it see the limitation in step 6.
- SQL Server: if the client gives a SQL login instead of rights for the service account, set
  `sql.auth: sql` and the `HFM_MON_NONPROD_SQL_*` secret (step 6). A self-signed SQL certificate
  needs `trust_server_certificate: true` for NONPROD (step 6, "SQL Server certificate").
- The application login check (slice 16) is not built yet; today's URL checks use no credentials.
- Personal credentials only for temporary testing, never for the service (D11).

## Troubleshooting

| Symptom | Meaning / action |
|---|---|
| Starts, then stops within seconds; event "start-up refused" | Configuration problem. Reason in the log; fix and start again (not restarted automatically) |
| Event 7031 / 7034, service restarts | Crash; traceback in the log. Recovery restarts it (60 / 120 / 300 s) |
| Log: "dashboard could not start" | Port in use or bind address not on this server (`monitoring` in environments.yaml); treated as a crash and retried |
| Error 1069 at start | Logon failed: wrong or changed password (user account: section 9); Log on as a service removed by a group policy; managed account: `Test-ADServiceAccount` fails |
| `install`: "account ... was not found" | Account name mistyped, or not visible from this server; nothing was installed |
| `install`: "is an ordinary user account: add --ask-password" | The account has a password; repeat with `--ask-password`. A managed account name ends with `$` |
| Error 1053 at start | Interpreter did not start: Python moved or removed, or not readable by the service account; run `hfm-service check` |
| `Test-ADServiceAccount`: "The server has rejected the client credentials" | Signed in with a local account (`whoami`), or the account is an ordinary user (see "Which account") |
| `pip install`: SSL error / "could not fetch URL https://pypi.org/..." | No internet access to PyPI from the server: offline install, step 3b |
| Offline upgrade: pip says "already satisfied", old behaviour remains | Add `--force-reinstall` (step 10) |
| Log: "maintenance.yaml changed but is invalid" | The edit was rejected and the previous windows still apply; fix the file (reason in the log) |
| WinRM checks "not reachable (ConnectionError)" although `Test-NetConnection` to the WinRM port succeeds | Check `hfm-service status`: account LocalSystem means the account change never took effect (row "Service runs, but as LocalSystem"). Check `winrm.port` / `use_ssl` (5985 = false, 5986 = true). Before release 0.1.1 also a proxy from the service account's settings (D35): upgrade |
| `check` / `install`: "interpreter is inside a user profile" | Python was installed "for me only". Install it for all users, `python -m venv .venv --clear` from that Python, `pip install .`, uninstall and install the service, set the account again (D36) |
| Service runs, but as LocalSystem although an account was set in services.msc | The service could not start as that account (often: Python in a profile, error 1053) and an older LocalSystem run was still going, or it was never restarted. `hfm-service status`, then `Restart-Service` and check the log |
| WinRM "authentication failed" / your own test gives 401 | That account has no WinRM rights on the node: Remote Management Users and Event Log Readers on every node. Test as the service account itself (sign in as it, or `Get-Credential`), not as yourself |
| The password prompt shows a blank line, or the console takes no typing | PowerShell ISE / remote console: use the services.msc route in step 7 |
| Log: "secret '...' not set" although it was set | Names in step 6 check: each secret needs a `_USERNAME` and a `_PASSWORD` line; then `Restart-Service` |
| SQL "SQL Server not reachable" with the SQL port open | Usually the SQL certificate is not trusted (self-signed): step 6, "SQL Server certificate" |
| SQL "SQL Server login failed" | SQL login name / password wrong or not enabled; with `auth: windows` the service account has no SQL login |
| URL check "HTTP 500" | The server itself answered with an error: repeat with `curl.exe -s -o NUL -w "%{http_code}" --noproxy "*" <url>`. If users can work normally, the probe path is not a valid page on its own: choose a path that answers 200 / 302 (`url_probes` in environments.yaml, restart). Otherwise the error is in that managed server's log on the web node |
| Every check Unknown | Monitoring access missing (A07-A10) or WebLogic secret not set (step 6); reasons in the log |
