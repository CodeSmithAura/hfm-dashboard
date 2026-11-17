"""Dashboard web app: thin routes over the presentation model.

    GET /                                     -> redirect to the first environment
    GET /env/{env}?sel=<component.node>       -> HTML dashboard (``partial=1``: content only, for refresh)
    GET /api/v1/environments                  -> [{id, name}]
    GET /api/v1/environments/{env}/dashboard  -> the view model as JSON (docs/ui.md, contract 1)
    GET /healthz                              -> service health: last poll per environment
    GET /static/{file}                        -> theme, layout and script

Look and layout are replaceable without code changes (docs/ui.md): ``ui_dir`` may hold
``templates/`` and ``static/`` files that take precedence over the built-in ones, file by file.
No external resources are loaded (the monitoring server may have no internet access).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import ChoiceLoader, Environment as Jinja, FileSystemLoader, select_autoescape

from hfm_dashboard.app.poller import PollSnapshot
from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.notices import MaintenanceWindow, Notice
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.presentation.dashboard import as_dict, build_dashboard

BUILTIN = Path(__file__).parent
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


class DashboardSource(Protocol):
    """What the web app needs from the running monitor (fakes in tests)."""

    def environments(self) -> list[tuple[str, str]]: ...
    def environment(self, env_id: str) -> Environment: ...
    def snapshot(self, env_id: str) -> PollSnapshot | None: ...
    def notices_since(self, env_id: str, since: datetime) -> Sequence[tuple[Notice, bool, str]]: ...
    def maintenance(self, env_id: str, at: datetime) -> Sequence[MaintenanceWindow]: ...


def _static_file(ui_dir: Path | None, name: str) -> Path | None:
    """A static file from ui_dir/static, else the built-in one; never outside those folders."""
    for base in ([ui_dir / "static"] if ui_dir else []) + [BUILTIN / "static"]:
        root = base.resolve()
        candidate = (root / name).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    return None


def create_app(
    source: DashboardSource,
    rules: Rules,
    ui_dir: str | Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    ui = Path(ui_dir) if ui_dir else None
    loaders = ([FileSystemLoader(str(ui / "templates"))] if ui else []) + [FileSystemLoader(str(BUILTIN / "templates"))]
    jinja = Jinja(loader=ChoiceLoader(loaders), autoescape=select_autoescape(["html"]), trim_blocks=True,
                  lstrip_blocks=True)

    app = FastAPI(title="HFM Availability Dashboard", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    def view(env_id: str, sel: str):
        if env_id not in dict(source.environments()):
            raise HTTPException(status_code=404, detail="unknown environment")
        now = clock()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return build_dashboard(
            env_id, source.environment(env_id), rules, source.snapshot(env_id), now, source.environments(),
            source.notices_since(env_id, start_of_day), source.maintenance(env_id, now), sel)

    @app.get("/", include_in_schema=False)
    def root():
        envs = source.environments()
        if not envs:
            raise HTTPException(status_code=404, detail="no environments")
        return RedirectResponse(f"/env/{envs[0][0]}", status_code=307)

    @app.get("/env/{env_id}", response_class=HTMLResponse)
    def page(env_id: str, sel: str = "", partial: int = 0):
        v = view(env_id, sel)
        template = "partials/content.html" if partial else "dashboard.html"
        return HTMLResponse(jinja.get_template(template).render(view=v))

    @app.get("/api/v1/environments")
    def api_environments():
        return [{"id": e, "name": n} for e, n in source.environments()]

    @app.get("/api/v1/environments/{env_id}/dashboard")
    def api_dashboard(env_id: str, sel: str = ""):
        return JSONResponse(as_dict(view(env_id, sel)))

    @app.get("/healthz")
    def health():
        now = clock()
        limit = timedelta(seconds=3 * rules.collection.poll_interval_seconds)
        polls = {}
        healthy = True
        for env_id, _ in source.environments():
            s = source.snapshot(env_id)
            polls[env_id] = s.observed_at.isoformat() if s else None
            healthy = healthy and s is not None and now - s.observed_at <= limit
        return JSONResponse({"status": "ok" if healthy else "stale", "last_poll": polls},
                            status_code=200 if healthy else 503)

    @app.get("/static/{name:path}", include_in_schema=False)
    def static(name: str):
        path = _static_file(ui, name)
        if path is None:
            raise HTTPException(status_code=404)
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    return app
