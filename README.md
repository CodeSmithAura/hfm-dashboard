# HFM Availability Dashboard

Live availability, stability score and caution notices for the Oracle HFM environment.

- Build rules for Claude Code and developers: `CLAUDE.md`
- Current status and open items: `docs/STATUS.md`
- Requirements, environment, decisions: `docs/`
- Behaviour configuration: `config/rules.yaml`
- Environment template (copy to `config/environments.yaml` on the server only): `config/environments.template.yaml`
- Owner list template (copy to `config/owners.yaml` on the server only): `config/owners.template.yaml`
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
.venv\Scripts\hfm-dashboard --config-dir config --once --env NONPROD --no-history
.venv\Scripts\hfm-dashboard --config-dir config                   # poll every poll_interval_seconds until Ctrl+C
```

Refuses to start on any configuration problem (exit code 2). Credentials come from environment
variables named after each secret in `environments.yaml` (interim until Q55), e.g.
`HFM_MON_PROD_WEBLOGIC_USERNAME` / `HFM_MON_PROD_WEBLOGIC_PASSWORD`; a missing one makes only that
collector's checks Unknown. SQL Server needs ODBC Driver 18 on the monitoring server.

History (poll scores, node states, status changes) is kept in `<data-dir>/history.sqlite`
(default `data/`), restored after a restart and purged after `history_retention_days`.
Use `--data-dir <folder>` to place it, or `--no-history` for a dry run that writes nothing.

Caution notices are worked out every poll (rules.yaml `notices:`), printed as `NOTICE` lines and emailed
through the SMTP relay in `environments.yaml`. Recipients come from `config/owners.yaml` (copy
`config/owners.template.yaml`, git-ignored). Without that file notices are logged as "owner list missing".
Notices of environments with `notices_enabled: false` are only logged. Check delivery before go-live:

```
.venv\Scripts\hfm-dashboard --config-dir config --test-email PROD     # one test email to the monitoring team
```

When updating the code on the server, update `config/rules.yaml` too: new keys are required and the
program refuses to start on an outdated file.

## Planned maintenance

Until the dashboard has a maintenance form (slice 15), planned windows go into `config/maintenance.yaml`
(copy `config/maintenance.template.yaml`, git-ignored): environment, optional component, start and end
with `Z` or an offset. Covered components send no notices and show as Maintenance. The file is re-read
on the next poll; an invalid edit keeps the previous windows and is logged - check the log after editing.

## Running as a Windows service

On the monitoring server the monitor runs as the Windows service `HfmAvailabilityMonitor`
(automatic start, restarted by Windows after a crash, log files per UTC day). Full steps:
`docs/service_install.md`. In short, from an elevated prompt:

```
.venv\Scripts\hfm-service check   --config-dir <abs>\config --data-dir <abs>\data --log-dir <abs>\logs
.venv\Scripts\hfm-service install --config-dir ... --data-dir ... --log-dir ... --account <DOMAIN>\<gmsa>$
Start-Service HfmAvailabilityMonitor
.venv\Scripts\hfm-service status | uninstall
```

Interim, with an ordinary domain user as the service account (D33): `--account <DOMAIN>\<user>
--ask-password` prompts for its password and hands it to Windows; update it in services.msc whenever
it changes. `environments.yaml` may hold only the environments you can reach (e.g. NONPROD alone), and
`load_balancer` may be left out where there is no front door (D34).

Recovery delays, stop timeout and log retention come from rules.yaml `monitor_service`.

## Dashboard

Served by the same service at `monitoring.dashboard_bind` / `dashboard_port` from `environments.yaml`
(`--no-web` to poll without it). JSON for other tools: `/api/v1/environments/{env}/dashboard`;
health check: `/healthz`. The look is replaceable without code changes: override files with
`--ui-dir <folder>` (see `docs/ui.md`). HTTPS and Windows login follow in slice 15 - until then keep
the port reachable only from trusted networks.

## Layout

Layout: `src/hfm_dashboard/domain` (status model, rules and environment models, check evaluation,
scoring - no I/O), `config/` (YAML loading, start-up gate), `ports/` (interfaces to external
systems), `adapters/` (WinRM, ... and their fakes), `app/` (collectors, poller, wiring), `presentation/`
(dashboard view model), `web/` (FastAPI app, templates, theme), `tools/` (operator commands). Tests mirror it under
`tests/`; masked sample responses live in `tests/fixtures/`.
