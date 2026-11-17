# Status (update as things change)

Last updated: 17 Nov 2026

## Plan
- Target go-live: 30 Nov 2026 (production), with shadow mode from 26 Nov.
- Live schedule with forecast dates: `docs/delivery_plan.xlsx` (dates recalculate from actuals).
- Current phase: Build 1, data collectors (planned 2-6 Nov). Forecast slips while design sign-off
  (A12) and access (A06-A11) are open.

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
  - Branch `feature/slice7-url-probes` (stacked on slice 6); not committed yet.

### Git
- Slices 1-4 on `feature/build1-core`, pushed to origin (17 Nov). No pull request yet.
- Slices 5, 6, 7 on `feature/slice5-service-check`, `feature/slice6-weblogic`, `feature/slice7-url-probes`,
  each stacked on the previous. Slices 5-6 pushed (17 Nov). One branch per slice; commit only after review.

### In progress
- Build 1 (data collectors), planned 2-6 Nov (running late).
- Waiting for server access (A06-A08, or permitted development access in the client environment).
  Then run `hfm-check-services --env NONPROD --record <dir>` there, review the masked files and bring
  them back for `tests/fixtures/winrm/`.
- Q53 (how Unknown nodes are scored) and Q54 (healthy HTTP codes) waiting for the application team.
- Q55 (production secret store) open with infrastructure / security; Q56 (non-prod WebLogic names) assumed.
- Q57 (how a load-balancer probe failure counts) open with the application team, high priority;
  Q58 (managed-server scheme for direct probes) assumed.
- Dev environment note: on this workstation Python 3.12 and git are not on PATH in new shells; use
  `.venv\Scripts\python` and the full git path, or fix PATH / turn off the Store python alias.

### Next steps (resume here)
1. Review and commit slice 7; push it.
2. When masked outputs arrive: add them to `tests/fixtures/winrm/` (and later `weblogic/`) and run the
   parse tests against them; fix an adapter if the real output differs from the synthetic format.
3. Next: SQL Server collector (pyodbc, DMVs and msdb) on its own branch with a fake and samples; then
   the poll loop wiring collectors -> node state -> scoring (no overlapping polls, see slice 7).
4. Open a pull request for `feature/build1-core` to `main` when convenient.
5. Login probe (later slice): decide how a failed login step (page loads, login rejected) is scored;
   not covered by rules.yaml yet.
6. Tell the application team that with the current assumed weights (Q26) one HFM node down gives
   94.67% (Amber), and an Admin Server outage stays Green (96%).

## Decisions pending
- A12: application team to confirm architecture option A (direct polling) in writing.

## Access and inputs still open (blocks running against production, not the build)
| Item | Action / query |
|---|---|
| Monitoring server (VM) | A06 |
| Read-only gMSA with WinRM rights + firewall rules | A07, A08 |
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
