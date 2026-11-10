# Status (update as things change)

Last updated: 10 Nov 2026

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
  - Committed on `feature/slice1-status-scoring`.
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

### In progress
- Build 1 (data collectors), planned 2-6 Nov (running late).
- Slice 3 not committed yet: needs review and a commit.
- Q53 (how Unknown nodes are scored) and Q54 (healthy HTTP codes) waiting for the application team.
- Dev environment note: on this workstation Python 3.12 and git are not on PATH in new shells; use
  `.venv\Scripts\python` and the full git path, or fix PATH / turn off the Store python alias.

### Next steps
1. Commit slice 3 after review.
2. Slice 4: first collector, Windows services over WinRM, with a fake and masked sample responses.
   It returns `ServiceObservation` / `CollectionFailure`; evaluation does the rest.
3. Login probe (later slice): decide how a failed login step (page loads, login rejected) is scored;
   not covered by rules.yaml yet.
4. Tell the application team that with the current assumed weights (Q26) one HFM node down gives
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
