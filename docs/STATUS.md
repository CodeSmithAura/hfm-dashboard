# Status (update as things change)

Last updated: 5 Nov 2026

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

### In progress
- Build 1 (data collectors), planned 2-6 Nov.
- Slice 1 is not committed yet: needs review and a commit.
- Q53 (how Unknown nodes are scored) waiting for the application team. Current setting `exclude`.

### Next steps
1. Commit slice 1 after review.
2. Slice 2: turn raw check results into Up / Degraded / Down using rules.yaml `checks:` (service states,
   WebLogic states and thresholds, URL response times and HTTP codes, SQL Server thresholds).
3. Slice 3: `environments.yaml` loader. Refuses to start on any `{{PLACEHOLDER}}`, checks that every
   rules.yaml component and node exists in the environment and has a service name per node.
4. Slice 4: first collector, Windows services over WinRM, with a fake and masked sample responses.
5. Tell the application team that with the current assumed weights (Q26) one HFM node down gives
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
