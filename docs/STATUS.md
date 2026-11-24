# Status (update as things change)

Last updated: 24 Nov 2026 (first run on the monitoring server against NONPROD; no-proxy fix D35)

## Plan
- Target go-live: 30 Nov 2026 (production), with shadow mode from 26 Nov.
- Live schedule with forecast dates: `docs/delivery_plan.xlsx` (dates recalculate from actuals).
- Build: slices 1-14 done and merged (collectors, poll loop, history, notices, email, dashboard);
  slices 17 (Windows service, smoke-tested) and 18 (maintenance file) merged 19 Nov (#9, #10).
  Slice 19 (NONPROD-only, domain user service account) 23 Nov. Remaining: 16 login probe, 15 HTTPS + login.
- Monitoring server (A06) provided 19 Nov. 23 Nov: production access delayed; the client gave access
  to NONPROD only, with an ordinary domain user account instead of the gMSA (interim, D33 / D34, Q64).
  Nothing has run against a real server yet; A09-A11 still open; design sign-off A12 open.

## Build progress

### Completed
- Slice 1 (4 Nov): status model, rules.yaml validation and scoring engine, 120 tests passing.
  - Status model: Up / Degraded / Down / Unknown. A node is Down only after
    `confirm_down_after_consecutive_failures` failed polls; recovery shows straight away; UTC enforced.
  - rules.yaml validation: unknown keys, duplicate keys, weights out of range, unordered points or bands
    and broken check references all stop the program. `auto_restart: false` and UTC storage enforced.
  - Scoring engine (pure function): node share, weighted average, hard-dependency cap, bands.
  - Project skeleton: `pyproject.toml`, `src/hfm_dashboard/{domain,config}`, `tests/`; a test keeps
    I/O libraries out of the domain. Setup steps in README.
  - New rules key `scoring.unknown_nodes` (ASSUMED Q53) and query Q53 added to the query log.
  - Design decisions D12-D16 recorded in `docs/decisions.md`.
  - Committed (`b5f03ae`).
- Slice 2 (10 Nov): check evaluation, 211 tests passing.
  - `domain/evaluation.py` (pure): typed observations per check type (service state, URL probe,
    WebLogic runtime, SQL Server metrics) -> Up / Degraded / Down / Unknown with a reason, all
    thresholds from rules.yaml `checks:`.
  - Missing metric or unlisted state -> Unknown; the current production disks (Q52) evaluate as Degraded.
  - New rules key `checks.url_probe.up_on_http_status` (ASSUMED Q54) and query Q54 added. Decision D17.
  - Committed (`f2cb744`).
- Slice 3 (10 Nov): environments.yaml loader and start-up gate, 248 tests passing.
  - `domain/environment.py`: strict model (nodes, roles, ports 1..65535, per-node service names,
    secret names) and cross-checks against rules.yaml; `nodes_of()` / `service_name()` for collectors.
  - `config/environment_loader.py`: refuses any `{{PLACEHOLDER}}` left in a value, lists each by
    path; clear error for an unquoted placeholder. `config/startup.py`: `load_configuration()` loads
    rules.yaml then environments.yaml and refuses to start on any problem (hard rule 6).
  - Tests fill the shipped template with invented values, proving template and rules.yaml agree.
  - Shared strict YAML reading moved to `config/yaml_loader.py`. Decision D18.
  - Committed (`b30e347`).
- Slice 4 (10 Nov): Windows services collector, 291 tests passing.
  - Port `ports/windows_services.py`; adapter `adapters/winrm_services.py` (pypsrp, read-only
    `Get-Service`, Name then DisplayName match, gMSA identity, no stored password); fake
    `adapters/fake_services.py`; collector `app/service_collector.py` (one query per host, in parallel,
    per-node service names, Unknown for an unreachable host).
  - New `winrm:` block (port placeholder, SSL, auth) in the environment template; new rules key
    `checks.windows_service.timeout_seconds` (ASSUMED Q40). Decision D19.
  - Sample responses in `tests/fixtures/winrm/` are synthetic (script output format); replace with
    masked recordings from the development servers when access allows.
  - Not run against a real server yet (needs A06-A08, or permitted development access).
  - Committed (`dec29de`).

- Slice 5 (17 Nov): operator command `hfm-check-services`, 322 tests passing.
  - `tools/check_services.py`: runs the services collector for one environment, prints component /
    node / status / reason (node keys, not hosts); exit 0 all Up, 1 not Up, 2 config problem.
  - `--record DIR`: masked raw responses per node plus a summary (`tools/masking.py`: hosts, short
    names, DNS suffixes, user name, IPv4); nothing written if anything real remains.
  - `--username` prompts for the password; refused for PROD (D11). Only the chosen environment's block
    of environments.yaml must be filled in (`only_environment` loader option). Decision D20.
  - Smoke-tested with the real pypsrp adapter against invented hosts: clean Unknown results, no crash.
  - Committed (`664d153`) on `feature/slice5-service-check`.
- Slice 6 (17 Nov): WebLogic collector, 380 tests passing.
  - Port `ports/weblogic.py`; adapter `adapters/weblogic_rest.py` (httpx; one read-only bulk search on
    the Admin Server: lifecycle state, health, stuck threads, heap); fake `adapters/fake_weblogic.py`;
    collector `app/weblogic_collector.py` (server name per node from environments.yaml).
  - rules.yaml: health map `health_up/degraded/down` (ASSUMED Q22), WARNING moved from lifecycle to
    health, STARTING / SHUTTING_DOWN Degraded, FAILED_NOT_RESTARTABLE Down, `timeout_seconds` (ASSUMED Q40).
  - environments template: `weblogic:` block (admin URL placeholder, server names per node); the
    cross-checks now cover WebLogic server names too.
  - Secret store port `ports/secrets.py`; interim adapter reads environment variables
    (`adapters/env_secrets.py`) until Q55 is decided. Queries Q55, Q56 added. Decision D21.
  - Synthetic REST samples in `tests/fixtures/weblogic/`; not run against a real Admin Server yet (A09).
  - Committed (`0573d48`) on `feature/slice6-weblogic`.
- Slice 7 (17 Nov): URL probe collector, 404 tests passing.
  - Port `ports/url_probe.py`; adapter `adapters/http_probe.py` (httpx GET, no credentials, redirects
    not followed, timed); fake `adapters/fake_probe.py`; collector `app/probe_collector.py`.
  - Per-node probes straight to each web node's managed-server port (`url_probes` block in the
    environment template; scheme ASSUMED Q58). Load-balancer probes reported separately under node
    `load_balancer`, kept out of scoring until Q57 is answered. Decision D22.
  - TLS verified against the Windows certificate store (`adapters/tls.py`, truststore), now also used
    by the WebLogic adapter.
  - Open point for the poll loop: `checks.url_probe.timeout_seconds` (60) equals
    `collection.poll_interval_seconds` (60), so a hanging probe can last a whole poll; the loop must
    not start overlapping polls, or the timeout should be lowered.
  - Committed (`a5f5fef`) on `feature/slice7-url-probes`.

- Slice 8 (17 Nov): SQL Server collector, 438 tests passing.
  - Port `ports/sql_health.py`; adapter `adapters/sql_health.py` (pyodbc, ODBC Driver 18, eight
    read-only SELECTs on DMVs and msdb; Windows auth via gMSA by default, SQL login optional); fake
    `adapters/fake_sql.py`; collector `app/sql_collector.py`.
  - Backup rule changed to `degraded_if_no_backup_in_last_hours` (msdb records only successful
    backups; ASSUMED Q59). New `checks.sql_server.timeout_seconds` (ASSUMED Q40). `sql:` block in the
    environment template. Decision D23.
  - Synthetic query samples in `tests/fixtures/sql/`; `production_disks.json` reproduces Q52 (C: 8.6%
    free -> Degraded). Not run against a real instance yet (A10); the monitoring server needs
    ODBC Driver 18 installed.
  - Committed (`fed1254`), merged into `main` (pull request #2).
- Slice 9 (17 Nov): poll loop, 467 tests passing.
  - `domain/snapshot.py` (pure): expected checks per node, `fold_poll` (worst check, missing check =
    Unknown, flap protection), scoring limited to the environment's nodes (`score(..., component_nodes)`).
  - `app/poller.py`: four collectors in parallel per poll, node states kept between polls, snapshot
    with results, load-balancer results (not scored), node states and score; overlapping polls skipped.
  - `app/wiring.py`: real adapters from configuration; a missing credential makes only that collector
    Unknown. `app/scheduler.py`: APScheduler job per environment, no overlap, missed runs coalesced.
  - `hfm-dashboard` entry point (`--once`, `--env`); library INFO logs lowered; UTC log timestamps.
  - Tests pin the numbers for the application team: one HFM node down 94.67% Amber, Admin Server down
    96% Green, SQL Server down capped 0% Red. Decision D24.
  - Smoke-tested with real adapters against invented hosts: every check Unknown with its reason.
  - Committed (`2deddbb`) on `feature/slice9-poll-loop`; pull request opened 17 Nov.
- Slice 10 (17 Nov): status history, 487 tests passing.
  - Port `ports/history.py`; adapter `adapters/sqlite_history.py` (SQLite, WAL, atomic write per poll,
    schema version check); fake `adapters/fake_history.py`.
  - Poll rows, current node states and confirmed status changes (`domain/snapshot.transitions`, also
    carried in each snapshot for the notice engine). States restored at start-up: "down since" and flap
    counters survive restarts, no spurious transitions. Purge every 6 h after `history_retention_days`.
  - `hfm-dashboard --data-dir <folder>` (default `data/`) and `--no-history`. Decision D25.
  - Smoke-tested: two real runs, states restored, rows written.
  - Decided 17 Nov (D26): URL probe failures that only show the host is unreachable from the
    monitoring server (DNS, no route, connect timeout, TLS / proxy) are Unknown; refused connections,
    read timeouts and 5xx stay Down. Consequence: a powered-off or isolated server shows Unknown on
    every check and drops out of the score, so slice 12 must warn when a node stays Unknown.
  - Committed (`1025bdc`, D26 change `563df18`); slices 9 and 10 merged into `main` (pull request #3, `cf6b3ff`).
- Slice 11 (17 Nov): disk space and event log checks, 537 tests passing.
  - Port `ports/host_health.py`; adapter `adapters/winrm_host_health.py` (one read-only script per host:
    local volumes, System log crash / unexpected-restart events, service display names); fake
    `adapters/fake_host_health.py`; collector `app/host_collector.py`; poller runs it as a fifth collector.
  - rules.yaml: `checks.disk` (Degraded < 10%, Down < 3%) and `checks.event_log` (60 min; crash IDs
    7031/7034/7023/7024, restart IDs 6008/41), `disk` and `event_log` on every enabled component
    (ASSUMED Q60). WinRM transport shared in `adapters/winrm_runner.py`. Decision D27.
  - With the production disk levels (Q52) the score is 88% Amber (SQL Server and SQL Agent Degraded).
  - Access: the monitoring account needs Event Log Readers on every server (add to the A07 request).
  - Committed (`8b30909`), merged into `main` (pull request #4, `8ed1415`).
- Slice 12 (17 Nov): notice engine, 585 tests passing.
  - `domain/notices.py` (pure): per-component state machine - first notice (15 min, 5 in close periods),
    reminders (30), one escalation (60), recovery; maintenance suppression with timer restart at window
    end; root-cause grouping under a fully Down hard dependency; 'cannot monitor' warning for nodes
    Unknown 15 min (D26); close calendar (first 7 / last 2 business days of each month, extra dates).
  - New rules keys `notices.notify_on_partial_outage: true` and `warn_when_unknown_after_minutes: 15`
    (ASSUMED Q61); `require_acknowledgement` must stay false for now.
  - `app/notice_tracker.py`: runs the engine per poll, restores/saves notice states (no repeat first
    notice after a restart), logs every notice with delivery outcome; `ports/maintenance.py` (no windows
    until the dashboard). History database schema 2 (version-1 files upgraded automatically).
  - Notices appear in snapshots and as `NOTICE` lines in the log; nothing is emailed yet (slice 13).
    NONPROD notices are logged only (`notices_enabled: false`). Decision D28.
  - Deployment note: rules.yaml must be updated with the code (new keys are required; an outdated file
    is refused at start-up - seen in the smoke test).
  - Committed (`9ae8493`), merged into `main` (pull request #5, `128002b`).
- Slice 13 (17 Nov): notice emails, 642 tests passing.
  - `domain/notice_text.py` (pure): subject, body (since / duration UTC, nodes with host and reason,
    grouped downstream components, close-period note, restart guidance) and recipients.
  - `config/owners.template.yaml` -> `owners.yaml` on the server (git-ignored): owners, managers (copied once
    escalated), default, monitoring team ('cannot monitor'). Missing file: service runs, notices logged as
    'owner list missing'. Invalid file: start refused.
  - `adapters/smtp_mail.py` (relay, port, STARTTLS, From from `environments.yaml notices`; no login);
    `app/notice_mailer.py` wired as the tracker's delivery. New keys: rules `notices.restart_order`
    (CONFIRMED Q35) and `smtp_timeout_seconds` (ASSUMED A13); environments `notices.smtp_port`
    (placeholder), `starttls`, optional `dashboard_url`.
  - `hfm-dashboard --test-email ENV` sends one test message to the monitoring team. Decision D29.
  - Tested with a fake SMTP object and a real smtplib session against a local test server.
  - Real sending needs the owner list (Q32 / A04) and relay access (A13).
  - Committed (`1645f41`), merged into `main` (pull request #6, `3d2ba95`).
- Dashboard mockup (17 Nov): clickable canvas with desktop, tablet and phone widths and sample
  scenarios; layout agreed by the user. Design-tool export of the desktop view kept as a reference in
  `docs/mockups/dashboard-desktop/` (not production code; loads web fonts and scripts from the internet).
- Slice 14 (17 Nov): dashboard web app, 687 tests passing.
  - `presentation/dashboard.py` (pure view model: statuses, labels, order, durations, banners);
    `/api/v1/environments/{env}/dashboard` serves it as JSON (contract 1, `docs/ui.md`).
  - `web/app.py` (FastAPI): page per environment with cell detail (`?sel=`), self-refresh every poll,
    `/healthz`; logic-free Jinja templates and a CSS-variable theme (dark default, light toggle) following
    the mockup; fluid down to phone width. `--ui-dir` overrides templates/static without code changes.
  - Security: CSP 'self' only, no inline styles/scripts, no external resources, escaped output, static
    files confined, no API docs. Times shown in the viewer's local time.
  - Runs in the service (uvicorn thread); `monitoring.dashboard_bind` / `dashboard_port` placeholders in
    the environment template; `--no-web`. Read-only: maintenance entry comes with login (slice 15).
  - Tested end to end with a real uvicorn server on localhost while polling. Decision D30.
  - Committed (`6013024`), merged into `main` (pull request #7, `c60ca9f`).

- Slice 17 (19 Nov): Windows service packaging, 742 tests passing.
  - Service `HfmAvailabilityMonitor` hosted with pywin32 (`adapters/windows_service.py`); body in
    `app/service_host.py` (runs the normal monitor loop with a stop event, testable without Windows'
    service manager). Registered command = base Python `-I -S service_boot.py <venv site-packages>
    <absolute folders>`, because a venv's python.exe is only a launcher (child process).
  - Requested stop = clean (running poll may finish within `stop_timeout_seconds`); start-up refused
    (configuration) = stopped with an error, not restarted; crash = Windows recovery restarts it.
    A dashboard that cannot bind its port now ends the monitor (exit 1) instead of polling without it.
  - Logs: `app/logs.py`, `<log-dir>/hfm-dashboard.log`, UTC timestamps, rotated 00:00 UTC; poll
    summaries go to the log; start / stop / failures also to the Application event log.
  - Operator command `hfm-service check | install | status | uninstall` (`tools/service_cli.py`,
    port `ports/service_control.py`, fake for tests): install refuses unless configuration is valid and
    the exact command line passes `--check`; sets automatic delayed start, gMSA account, recovery
    actions (`sc.exe failure`), event log source.
  - New rules block `monitor_service` (restart delays 60/120/300 s, reset 24 h, stop timeout 90 s, logs
    kept 30 days; ASSUMED Q62). Decision D31. Runbook `docs/service_install.md`.
  - Verified here: the registered command line in `--check` mode (base interpreter, isolated) loads the
    venv, pywin32 and configuration; the service body runs and stops the real loop.
  - Smoke test 19 Nov (user's workstation, elevated, LocalSystem, invented hosts, dashboard on
    localhost) - all passed: install with recovery actions; start, dashboard listening, polls logged
    (132 polls over ~3 h 20 min), `/healthz` 200; clean stop ("service stopped"); process killed ->
    Windows restarted it after ~70 s; invalid maintenance.yaml -> start-up refused, event 7024, not
    restarted; uninstall. pywin32 hosting with the base interpreter works, so the WinSW fallback (D31)
    is not needed. Still to see on the real server: running as the gMSA (logon right, folder access).
  - Committed (`b67337f`), merged into `main` (pull request #9, `b7a284a`).
- Slice 18 (19 Nov): interim maintenance windows file, 768 tests passing.
  - `config/maintenance.template.yaml` -> `maintenance.yaml` on the server (git-ignored): environment,
    optional component, start/end with a zone (UTC inside), reason. `domain/maintenance_file.py` (model,
    checks), `config/maintenance_loader.py`, `adapters/file_maintenance.py` behind the existing
    `MaintenanceCalendar` port; wired into the notice engine and the dashboard.
  - Re-read on the next poll after an edit; invalid at start = start refused; invalid edit = previous
    windows kept, error logged once; missing file = no windows. `hfm-service check` validates it (and
    owners.yaml) too.
  - New rules keys `maintenance.windows_file` and `max_window_hours: 72` (ASSUMED Q63). Decision D32.
  - Committed (`970988b`, mockup move `656f9e8`), merged into `main` (pull request #10, `789b372`).
- Slice 19 (23 Nov): interim NONPROD-only operation with a domain user service account, 794 tests passing.
  - `hfm-service install --account DOMAIN\name --ask-password`: password prompted twice (not echoed),
    asked only after the self-check passed, handed to the service manager, never printed, logged or in
    `repr`. An ordinary user account without `--ask-password` is refused (error 1069 otherwise);
    `--ask-password` for a gMSA / sMSA / LocalSystem is refused. Every named account is granted
    'Log on as a service' locally before anything is registered (a mistyped account installs nothing).
    Port `ServiceControl.grant_service_logon` (pywin32: LsaAddAccountRights); fake records it. Decision D33.
  - `load_balancer` optional per environment: left out = no load-balancer probes, dashboard note "No
    load balancer configured for this environment"; masking copes without it. The template leaves it
    out for NONPROD. A NONPROD-only `environments.yaml` is valid (test). Decision D34.
  - Runbook `docs/service_install.md`: "Which account" (managed vs ordinary user), domain sign-in
    prerequisite, `{{MONITOR_ACCOUNT}}` placeholder, NONPROD secret example, password change (section 9),
    switching to the managed account, "Interim: NONPROD only", new troubleshooting rows. Query Q64.
  - Found on the server 23 Nov: `Test-ADServiceAccount` / `Install-ADServiceAccount` failed ("server
    has rejected the client credentials", then "object could not be found") because the account given
    is an ordinary user, not a managed account; `Invoke-Command -Credential` to a NONPROD node with
    that account listed the HFM services (WinRM path and rights fine for that node).
  - Committed (`2c552c4`), merged into `main` (pull request #12); offline runbook pull request #13;
    release `v0.1.0` with an offline wheelhouse.
- Server run 24 Nov (NONPROD, real servers, first time): PyPI access granted, `pip install .` worked.
  `--ask-password` could not be used: the hidden prompt shows a blank line in ISE, and the plain console
  of the user's remote session takes no typing (paste only). Workaround: install without `--account`,
  then set the account and password in services.msc (grants Log on as a service). Service running,
  polls logged, dashboard served. All eight ports open from the monitoring server. Findings:
  - WinRM checks on every node "not reachable (ConnectionError)" with correct settings -> proxy taken
    from the service account's settings; fixed by D35 (below).
  - HFM Web `/hfmadf/` answers HTTP 500 directly (Workspace and FR Web 200): probe path or a real HFM
    Web error - to confirm whether users can work, then pick a path answering 200 / 302.
  - WebLogic and SQL Unknown: credentials / rights not received yet (A09, A10).
- Fix (24 Nov, D35): no proxy for WinRM, URL probes and WebLogic REST; tests fail without the fix,
  798 tests passing.

### Git
- Slices 1-14, 17 and 18 are in `main` (latest merge: pull request #11, `d69723e`, 19 Nov). Merges use merge
  commits so each slice commit stays visible.
- Each new slice branches off `main` and merges back after review. Commit only after review.
- No GitHub CLI on this workstation: pull requests are opened from a pre-filled compare link.

### In progress
- Build 1 (data collectors), planned 2-6 Nov (running late).
- Waiting for server access (A06-A08, or permitted development access in the client environment).
  Then run `hfm-check-services --env NONPROD --record <dir>` there, review the masked files and bring
  them back for `tests/fixtures/winrm/`.
- Q53 (how Unknown nodes are scored) and Q54 (healthy HTTP codes) waiting for the application team.
- Q55 (production secret store) open with infrastructure / security; Q56 (non-prod WebLogic names) assumed.
- Q57 (how a load-balancer probe failure counts) open with the application team, high priority;
  Q58 (managed-server scheme for direct probes) assumed. Q59 (backup check / schedule) assumed.
- Q60 (disk / event log thresholds, and whether low disk lowers every component on a server) assumed.
- Q61 (notice rules: partial outages notify, Unknown warning, close calendar, grouping) assumed, high priority.
- Dev environment note: on this workstation Python 3.12 and git are not on PATH in new shells; use
  `.venv\Scripts\python` and the full git path, or fix PATH / turn off the Store python alias.

### Slice 15 deferred (decided 17 Nov)
HTTPS, Windows/AD login (viewer/admin groups) and the maintenance-window entry form wait until their
inputs exist: AD group names, a TLS certificate for the dashboard, confirmation the monitoring server
is domain-joined. Consequences and mitigations until then:
- The dashboard is plain HTTP without login (read-only, but shows status and host names). Set
  `monitoring.dashboard_bind: localhost`, or allow only trusted admin machines in the firewall.
  HTTPS + Windows login is a stated requirement: needed before go-live on 30 Nov, or accepted
  explicitly as a shadow-mode-only gap.
- Maintenance windows cannot be entered on the dashboard. Mitigation in place (slice 18): the
  monitoring team writes them into `config/maintenance.yaml` on the server (process to agree, Q63).

### Next steps (resume here)
1. NONPROD on the monitoring server (service running since 24 Nov): deploy the D35 fix (`git pull`,
   `pip install .`, restart) and confirm the WinRM checks turn Up; settle the HFM Web probe path
   (HTTP 500 at `/hfmadf/`); WebLogic / SQL once A09 / A10 arrive. Possible follow-up: `hfm-service`
   password entry that works without a console (ISE, paste-only sessions) - offered, not started. Then
   shadow mode prep: owners.yaml with the monitoring team only, agree the maintenance process (Q63).
   Ask the client Q64 (permanent account, password expiry) and when production access comes.
2. Decide Q55 (secret store) before go-live; until then the WebLogic secret sits in the service's
   registry Environment value (readable by local users).
3. When masked outputs arrive: add them to `tests/fixtures/` (winrm, weblogic, sql) and run the parse
   tests against them; fix an adapter if the real output differs from the synthetic format.
4. Slice 16: login probe (Playwright) once the test user exists (A11 / Q20); decide first how a failed
   login step (page loads, login rejected) is scored - not covered by rules.yaml yet.
5. Slice 15: HTTPS, AD login, maintenance form - when its inputs arrive (see above).
6. Tell the application team: with the assumed weights (Q26) one HFM node down = 94.67% Amber, Admin
   Server down = 96% Green; today's production disk levels (Q52) start the score at 88% Amber.
7. Chase high-priority answers: Q57 (load-balancer probe scoring), Q61 (notice rules, esp. whether one
   node down emails owners), Q32 (owner list), A13 (SMTP relay).

### Server inputs to fill on the monitoring server (cumulative)
- `environments.yaml` from the template: hosts, ports, `winrm`, `weblogic` (admin URL), `url_probes`,
  `sql`, `notices` (relay, port, from), `monitoring` (monitor host, dashboard bind and port).
- `owners.yaml` from `owners.template.yaml` once the owner list exists (Q32).
- `maintenance.yaml` from `maintenance.template.yaml` when planned work is announced (optional).
- Secrets as environment variables until Q55 (e.g. `HFM_MON_PROD_WEBLOGIC_USERNAME` / `_PASSWORD`).
- Monitoring account: WinRM read access and Event Log Readers on every server (A07), WebLogic Monitor
  role (A09), SQL VIEW SERVER STATE + msdb read (A10). ODBC Driver 18 on the monitoring server.
- Always deploy `config/rules.yaml` together with the code (new keys are required at start-up).
- 23 Nov: the monitoring server cannot reach PyPI (SSL error on `pip install .`): install offline from
  a wheelhouse built on the workstation (runbook step 3b; verified here with `--no-index` into a
  fresh venv). Offline upgrades need `--force-reinstall` while the version stays 0.1.0.
- Python 3.12 for all users on the monitoring server; service account (gMSA planned, interim domain
  user with `--ask-password`, D33) - install grants Log on as a service; install steps in
  `docs/service_install.md`. Shadow mode: owners.yaml lists only the monitoring team.

## Decisions pending
- A12: application team to confirm architecture option A (direct polling) in writing.

## Access and inputs still open (blocks running against production, not the build)
| Item | Action / query |
|---|---|
| Monitoring server (VM) | A06 - provided 19 Nov |
| Read-only gMSA with WinRM rights + firewall rules | A07, A08 - interim: domain user account, NONPROD only (D33, Q64); production access delayed |
| WebLogic read-only monitor user | A09 |
| SQL Server read-only login (VIEW SERVER STATE, msdb read) | A10 |
| Test user for the login probe | A11, Q20 |
| Production load balancer URL | Q17 |
| Component owner list | A04, Q32 |
| SMTP relay access | A13 |
| Month-end close dates | Q46 |
Requests for the monitoring server and WinRM access have been drafted for the infrastructure team.

## Issues raised with other teams
- Q52: production SQL Server C: drive 8.6% free (below the 10% Degraded threshold); data drive 11.6%;
  SQL Server Agent set to Manual start.
- Q50: HFM web services route only to web node 2.
- Q51: HFM nodes 2 and 3 run in a different time zone.

## Build approach until access arrives
- Build and test every collector against fakes and the masked sample data.
- Where permitted, test Windows checks against the development servers.
