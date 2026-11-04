# HFM Availability Dashboard

Monitors an Oracle HFM 11.2.25 environment (Windows Server 2022), calculates one application
stability score, shows it on an internal web dashboard and emails caution notices to component
owners until a failed component is restored.

This file holds the stable rules for building it. Current status, dates and open items live in
`docs/STATUS.md`; read that at the start of a session. Background: `docs/requirements.md`,
`docs/environment.md`, `docs/decisions.md`.

## Hard rules (never break)

1. **No real infrastructure identifiers in the repository.** No server names, IP addresses, domain
   names, account names or port numbers in code, tests, docs or commit messages. Use the
   placeholders in `config/environments.template.yaml` (`{{WEB_NODE_01}}`, `{{SQL_PORT}}`, ...).
   Real values exist only in `config/environments.yaml` on the target server (git-ignored).
   Test fixtures use invented names such as `web-node-01.example.test`.
2. **No credentials anywhere in files.** Code reads secrets by name (`secrets:` in the environment
   config) from a secret-store adapter. Never log a secret or a full connection string.
3. **Read-only against HFM.** Collectors only query. Never start, stop or restart a service, change
   a configuration, or write to an HFM database. `auto_restart` stays false.
4. **`config/rules.yaml` is the single source of behaviour.** Thresholds, weights, timings, bands
   and calendars are read from it, never hard-coded. Each value carries a `CONFIRMED Qnn` or
   `ASSUMED Qnn` basis comment; keep that convention when adding keys.
5. **All times are stored and compared in UTC.** Servers run in different time zones.
6. **The program refuses to start** if `environments.yaml` still contains any `{{PLACEHOLDER}}` or
   if `rules.yaml` fails validation (unknown keys, missing components, weights out of range).

## Architecture (decided: option A, direct polling - see docs/decisions.md)

A single Python service on a dedicated Windows monitoring server:

```
collectors -> status store -> scoring engine -> notice engine -> dashboard (web)
(WinRM services/disk/events, URL + login probes, WebLogic REST, SQL Server DMVs)
```

- Ports and adapters: the domain (status model, scoring, notice state machine) has no imports from
  WinRM, HTTP, SQL or SMTP libraries. Each external system is an adapter behind an interface, so
  every adapter can be replaced by a fake in tests.
- **Scoring is a pure function**: (current node statuses, rules) -> component scores, overall score,
  band. No I/O. Fully unit-tested, including the hard-dependency cap and node-share cases.
- **Notice engine is a state machine** per component: down_since, first_notice_sent, reminders,
  escalated, recovered. Driven by rules.yaml timings and the close calendar; suppressed in
  maintenance windows; downstream notices grouped under a root-cause hard dependency.
- Status history is stored off the EPM SQL Server (it is a single point of failure). Default:
  SQLite on the monitoring server, behind a repository interface.
- New Relic is not used (it watches no EPM service in production).

## Stack

Python 3.12, FastAPI, APScheduler, pydantic, PyYAML, httpx, pypsrp or pywinrm, pyodbc
(ODBC Driver 18), Playwright (Chromium) for the login probe, pytest. Runs as a Windows service.

## Domain facts the code must respect

- Status per node: Up / Degraded / Down (plus Unknown when a check cannot run) -> 100 / 50 / 0 points.
- A node is Down only after `confirm_down_after_consecutive_failures` failed polls.
- Multi-node components score by node share; a hard dependency that is Down caps the overall score.
- Windows service names differ per node in production (suffix 1/2/3); always look them up per node
  from `environments.yaml`, never derive them.
- Components and nodes: see `docs/environment.md`. FR RMI and LCM are intentionally not monitored.

## Working conventions

- Small vertical slices; each slice ends with passing tests.
- Every collector ships with a fake and recorded sample responses (masked) for tests.
- Run `pytest` before declaring a task done; add tests for every rules.yaml key you consume.
- When a requirement is unclear, check `docs/query_log.csv` (status and design decision per
  question) before asking.
