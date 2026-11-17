# HFM Availability Dashboard

Live availability, stability score and caution notices for the Oracle HFM environment.

- Build rules for Claude Code and developers: `CLAUDE.md`
- Current status and open items: `docs/STATUS.md`
- Requirements, environment, decisions: `docs/`
- Behaviour configuration: `config/rules.yaml`
- Environment template (copy to `config/environments.yaml` on the server only): `config/environments.template.yaml`
- Read-only discovery and pre-flight scripts: `tools/discovery/`
- Query log and live delivery plan: `docs/query_log.csv`, `docs/delivery_plan.xlsx`

## Development

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

## Checking Windows services by hand (read-only)

Needs `config/rules.yaml` and a `config/environments.yaml` in which at least the chosen
environment's block is filled in. Output shows node keys, not host names.

```
.venv\Scripts\hfm-check-services --env NONPROD
.venv\Scripts\hfm-check-services --env NONPROD --record recordings
.venv\Scripts\hfm-check-services --env NONPROD --username DOMAIN\dev.user   # dev testing only (D11); not for PROD
```

`--record DIR` writes each host's raw response plus a summary, with host names, DNS suffixes, the
user name and IP addresses masked; nothing is written if anything real is left. Review the files,
then copy them to `tests/fixtures/winrm/`. Exit code: 0 all Up, 1 something not Up, 2 config problem.

## Running the monitor

```
.venv\Scripts\hfm-dashboard --config-dir config --once            # one poll per environment, print scores
.venv\Scripts\hfm-dashboard --config-dir config --once --env NONPROD
.venv\Scripts\hfm-dashboard --config-dir config                   # poll every poll_interval_seconds until Ctrl+C
```

Refuses to start on any configuration problem (exit code 2). Credentials come from environment
variables named after each secret in `environments.yaml` (interim until Q55), e.g.
`HFM_MON_PROD_WEBLOGIC_USERNAME` / `HFM_MON_PROD_WEBLOGIC_PASSWORD`; a missing one makes only that
collector's checks Unknown. SQL Server needs ODBC Driver 18 on the monitoring server.

## Layout

Layout: `src/hfm_dashboard/domain` (status model, rules and environment models, check evaluation,
scoring - no I/O), `config/` (YAML loading, start-up gate), `ports/` (interfaces to external
systems), `adapters/` (WinRM, ... and their fakes), `app/` (collectors), `tools/` (operator commands). Tests mirror it under
`tests/`; masked sample responses live in `tests/fixtures/`.
