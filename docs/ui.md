# Dashboard UI: how it is built and how to redesign it

The dashboard's look and layout are deliberately separate from the monitoring logic. Every decision
about *what* is shown - statuses, wording, order, durations, banners - is made in one pure Python
module, `src/hfm_dashboard/presentation/dashboard.py`. The page only *prints* that view model, and
the same model is served as JSON. A redesign therefore never touches scoring, notices or collectors.

```
collectors -> poller -> snapshot ---\
history (notices) -------------------> presentation/dashboard.py (view model) -> templates + theme (HTML)
maintenance windows ----------------/                                       \-> /api/v1/... (JSON)
```

## Three ways to change the UI (smallest first)

1. **Re-skin (colours, fonts, radius, light/dark):** override `static/theme.css`. Every colour and
   font the layout uses is a CSS variable there.
2. **Re-layout (move, drop or add panels):** override any template or static file with
   `hfm-dashboard --ui-dir <folder>`. The folder may contain `templates/` and `static/`; each file
   found there replaces the built-in file of the same name, everything else stays built-in. Start
   by copying the file you want to change from `src/hfm_dashboard/web/templates/` or `static/`.
3. **A different front end entirely (SPA, Grafana panel, another tool):** read the JSON API below.

Rules a custom UI must keep (the security policy enforces most of them):

- No inline `<style>`, `style="..."` attributes or inline scripts: the Content Security Policy allows
  only files served from `/static/`.
- No external resources (fonts, CDNs): the monitoring server may have no internet access.
- No logic in templates: print fields, loop over lists, branch on fields, `| length`. If a design
  needs a new value (for example a percentage formatted differently), add a field to the view model
  and a test for it - do not compute it in the template. `tests/web/test_web_app.py` checks this.

## Pages and endpoints

| Path | Returns |
|---|---|
| `/` | redirect to the first environment |
| `/env/{env}` | HTML dashboard; `?sel=<component>.<node>` selects a grid cell; `?partial=1` returns only the refreshable content (used by the page every poll interval) |
| `/api/v1/environments` | `[{"id": "PROD", "name": "Production"}, ...]` |
| `/api/v1/environments/{env}/dashboard` | the view model as JSON (`?sel=` as above) |
| `/healthz` | `{"status": "ok" or "stale", "last_poll": {env: iso}}`, HTTP 503 when a poll is older than 3 intervals |
| `/static/{file}` | `theme.css`, `dashboard.css`, `dashboard.js` (or your overrides) |

## JSON contract, version 1

All times are ISO 8601 UTC strings (`""` when not known); show them in the viewer's local time.
Status values everywhere: `up`, `degraded`, `down`, `unknown`, `maintenance`, `none` (no data yet).
Fields are only ever added within a version; a removal or change of meaning bumps `contract_version`.

| Field | Meaning |
|---|---|
| `contract_version` | `1` |
| `environment`, `environment_name`, `notices_enabled` | the environment shown |
| `environments[]` | tabs: `id`, `name`, `active` |
| `observed_at`, `generated_at`, `poll_interval_seconds` | poll shown, response time, refresh interval |
| `score` | `pct` (number or null), `pct_text`, `band` (`green`/`amber`/`red`/`unknown`), `band_label`, `capped_by[]` (component names) |
| `counts` | `total`, `up`, `degraded`, `down`, `unknown` (components) |
| `banners[]` | `kind` (`cap`, `maintenance`, `notices_off`, `stale`, `no_data`) and ready-to-show `text` |
| `nodes[]` | grid columns: `id`, `label` |
| `rows[]` | grid rows: `id`, `name`, `meta`, `hard_dependency`, `status`, `cells[]` |
| `rows[].cells[]` | `node`, `applicable` (false: component does not run there), `status`, `label`, `select`, `selected`, `aria` |
| `selected` | null, or `component`, `name`, `node`, `node_label`, `host`, `status`, `status_label`, `since`, `since_text`, `checks[]` (`check`, `label`, `status`, `status_label`, `detail`) |
| `attention[]` | worst first: `component`, `name`, `node_label`, `status`, `status_label`, `detail`, `since`, `duration_text` |
| `notices[]` | today, newest first: `at`, `kind`, `kind_label`, `tone` (a status for styling), `component`, `name`, `text`, `delivery` |
| `load_balancer[]` | probes through the load balancer (not scored, Q57): `check`, `name`, `path`, `status`, `status_label`, `detail` |
| `lb_note` | explanation shown next to the load balancer probes |
| `component_scores[]` | `id`, `name`, `weight`, `pct`, `pct_text`, `status` |

The layout follows the agreed mockup (17 Nov): top bar with environment tabs and theme toggle,
KPI cards, component x node grid with cell detail, attention list, notices, load balancer probes and
component scores. It is fluid from phone width (cells shrink to icons, the grid scrolls in its box)
to wide screens.
