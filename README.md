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

Layout: `src/hfm_dashboard/domain` (status model, rules and environment models, check evaluation,
scoring - no I/O), `config/` (YAML loading, start-up gate), `ports/` (interfaces to external
systems), `adapters/` (WinRM, ... and their fakes), `app/` (collectors). Tests mirror it under
`tests/`; masked sample responses live in `tests/fixtures/`.
