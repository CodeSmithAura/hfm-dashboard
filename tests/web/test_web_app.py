"""Dashboard web app: routes, JSON contract, security headers, replaceable UI, logic-free templates."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hfm_dashboard.app.dashboard_source import LiveDashboardSource
from hfm_dashboard.app.poller import Poller
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.web.app import BUILTIN, create_app
from tests.app.test_poller import HFM2, Clock, healthy_collectors
from tests.conftest import FILLED


@pytest.fixture(scope="module")
def envs(repo_rules):
    return parse_environments(FILLED, repo_rules).environments


def make_client(envs, rules, ui_dir=None, poll=True, hfm_down=False, age=timedelta(0)):
    pollers = {}
    for name in ("PROD", "NONPROD"):
        c = healthy_collectors(envs[name], rules)
        if hfm_down and name == "PROD":
            c.services.states[HFM2]["HyS9FinancialManagementJavaServer_EPMHFM2"] = "Stopped"
        p = Poller(name, envs[name], rules, c, clock=Clock())
        if poll:
            p.poll_once()
            p.poll_once()
        pollers[name] = p
    source = LiveDashboardSource(pollers, envs)
    latest = pollers["PROD"].latest
    now = (latest.observed_at if latest else Clock()()) + age
    return TestClient(create_app(source, rules, ui_dir=ui_dir, clock=lambda: now))


@pytest.fixture
def client(envs, repo_rules):
    return make_client(envs, repo_rules, hfm_down=True)


class TestPages:
    def test_root_redirects_to_first_environment(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/env/PROD"

    def test_dashboard_page(self, client):
        r = client.get("/env/PROD")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        html = r.text
        assert "94.67%" in html and "HFM Server (cluster)" in html and 'aria-current="page"' in html
        assert 'data-refresh-seconds="60"' in html

    def test_selection_shows_detail(self, client):
        html = client.get("/env/PROD?sel=epmhfm.hfm_node_02").text
        assert "hfm-node-02.example.test" in html and "service Stopped" in html

    def test_partial_is_content_only(self, client):
        html = client.get("/env/PROD?partial=1").text
        assert "<html" not in html and "94.67%" in html

    def test_unknown_environment_404(self, client):
        assert client.get("/env/UAT").status_code == 404
        assert client.get("/api/v1/environments/UAT/dashboard").status_code == 404

    def test_escaping(self, envs, repo_rules):
        c = make_client(envs, repo_rules)
        html = c.get("/env/PROD?sel=<script>alert(1)</script>").text
        assert "<script>alert(1)</script>" not in html


class TestApi:
    def test_environments(self, client):
        assert client.get("/api/v1/environments").json() == [
            {"id": "PROD", "name": "Production"}, {"id": "NONPROD", "name": "Non-Production (Development)"}]

    def test_dashboard_json_contract(self, client):
        data = client.get("/api/v1/environments/PROD/dashboard?sel=epmhfm.hfm_node_02").json()
        assert data["contract_version"] == 1
        assert data["score"]["pct"] == 94.67 and data["score"]["band"] == "amber"
        assert data["selected"]["status"] == "down"
        assert data["attention"][0]["node_label"] == "HFM 2"

    def test_health_ok_then_stale(self, envs, repo_rules):
        ok = make_client(envs, repo_rules).get("/healthz")
        assert ok.status_code == 200 and ok.json()["status"] == "ok"
        stale = make_client(envs, repo_rules, age=timedelta(minutes=10)).get("/healthz")
        assert stale.status_code == 503 and stale.json()["status"] == "stale"

    def test_before_first_poll(self, envs, repo_rules):
        c = make_client(envs, repo_rules, poll=False)
        assert "Waiting for the first poll" in c.get("/env/PROD").text
        assert c.get("/healthz").status_code == 503


class TestSecurity:
    def test_headers(self, client):
        h = client.get("/env/PROD").headers
        assert "default-src 'self'" in h["content-security-policy"]
        assert "'unsafe-inline'" not in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff" and h["cache-control"] == "no-store"

    def test_no_api_docs_exposed(self, client):
        assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404

    @pytest.mark.parametrize("path", ["/static/../app.py", "/static/..%2Fapp.py", "/static/%2e%2e/app.py",
                                      "/static/templates/base.html", "/static/nope.css"])
    def test_static_cannot_escape(self, client, path):
        assert client.get(path).status_code == 404

    def test_no_external_resources(self):
        for f in list((BUILTIN / "templates").rglob("*.html")) + list((BUILTIN / "static").glob("*")):
            text = f.read_text(encoding="utf-8")
            assert not re.search(r"(src|href)=\"https?://", text), f.name
            assert "fonts.googleapis" not in text and "@import" not in text, f.name

    def test_no_inline_styles_or_scripts(self):
        for f in (BUILTIN / "templates").rglob("*.html"):
            text = f.read_text(encoding="utf-8")
            assert " style=" not in text and "<style" not in text, f.name
            assert re.search(r"<script(?![^>]*\bsrc=)", text) is None, f.name


class TestStatic:
    @pytest.mark.parametrize("name", ["theme.css", "dashboard.css", "dashboard.js"])
    def test_served(self, client, name):
        r = client.get(f"/static/{name}")
        assert r.status_code == 200 and len(r.content) > 100


class TestReplaceableUi:
    def test_ui_dir_overrides_templates_and_static_file_by_file(self, envs, repo_rules, tmp_path):
        (tmp_path / "templates" / "partials").mkdir(parents=True)
        (tmp_path / "templates" / "partials" / "content.html").write_text(
            "<p class='mine'>Score {{ view.score.pct_text }} ({{ view.score.band_label }})</p>", encoding="utf-8")
        (tmp_path / "static").mkdir()
        (tmp_path / "static" / "theme.css").write_text(":root{--bg:#000}", encoding="utf-8")
        c = make_client(envs, repo_rules, ui_dir=tmp_path, hfm_down=True)
        html = c.get("/env/PROD").text
        assert "<p class='mine'>Score 94.67% (Amber)</p>" in html      # custom content, built-in shell
        assert "HFM Availability" in html                               # base/dashboard templates still built-in
        assert c.get("/static/theme.css").text == ":root{--bg:#000}"
        assert c.get("/static/dashboard.js").status_code == 200        # not overridden: built-in

    def test_templates_contain_no_logic(self):
        """Templates may print fields, loop, branch on fields and use |length - nothing else."""
        allowed_expr = re.compile(r"^[\w.]+( or (\"[^\"]*\"|\d+))?( \| length)?$")
        allowed_stmt = re.compile(r"^(for \w+ in [\w.]+|endfor|if (not )?[\w.]+|else|endif|"
                                  r"extends \"[\w./]+\"|include \"[\w./]+\"|block \w+|endblock)$")
        for f in (BUILTIN / "templates").rglob("*.html"):
            text = f.read_text(encoding="utf-8")
            for expr in re.findall(r"\{\{\s*(.*?)\s*\}\}", text):
                assert allowed_expr.match(expr), f"{f.name}: {{{{ {expr} }}}}"
            for stmt in re.findall(r"\{%-?\s*(.*?)\s*-?%\}", text):
                assert allowed_stmt.match(stmt), f"{f.name}: {{% {stmt} %}}"
