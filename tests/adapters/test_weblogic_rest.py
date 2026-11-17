"""WebLogic REST adapter against httpx.MockTransport (no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from hfm_dashboard.adapters.weblogic_rest import SEARCH_PATH, SEARCH_QUERY, WeblogicRestSource, parse_search
from hfm_dashboard.ports.secrets import Secret
from hfm_dashboard.ports.weblogic import WeblogicQueryError
from tests.conftest import FIXTURES

SAMPLES = FIXTURES / "weblogic"
ADMIN = "https://wls-admin.example.test:1234/"
CRED = Secret("monitor-user", "pw-not-real")


def sample(name: str):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def source(handler, **kw) -> WeblogicRestSource:
    return WeblogicRestSource(ADMIN, CRED, timeout_seconds=20, transport=httpx.MockTransport(handler), **kw)


class TestRequest:
    def test_single_read_only_search_request(self):
        seen: list[httpx.Request] = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=sample("domain_all_running.json"))

        servers = source(handler).fetch_servers()
        assert len(servers) == 7
        (req,) = seen
        assert req.method == "POST"
        assert str(req.url) == "https://wls-admin.example.test:1234" + SEARCH_PATH
        assert req.url.path.endswith("/domainRuntime/search")      # search only reads
        assert json.loads(req.content) == SEARCH_QUERY
        assert req.headers["X-Requested-By"] == "hfm-dashboard"
        assert req.headers["Authorization"].startswith("Basic ")

    def test_query_asks_only_for_runtime_fields(self):
        children = SEARCH_QUERY["children"]
        assert set(children) == {"serverLifeCycleRuntimes", "serverRuntimes"}
        assert set(children["serverRuntimes"]["children"]) == {"threadPoolRuntime", "JVMRuntime"}

    @pytest.mark.parametrize(("status", "expected"), [
        (401, "authentication failed"), (403, "Monitor role"), (500, "HTTP 500"), (404, "HTTP 404"),
    ])
    def test_http_errors(self, status, expected):
        with pytest.raises(WeblogicQueryError, match=expected):
            source(lambda r: httpx.Response(status, text="secret stack trace")).fetch_servers()

    def test_not_json(self):
        with pytest.raises(WeblogicQueryError, match="not JSON"):
            source(lambda r: httpx.Response(200, text="<html>login</html>")).fetch_servers()

    def test_connect_error(self):
        def handler(request):
            raise httpx.ConnectError("refused by wls-admin.example.test")
        with pytest.raises(WeblogicQueryError) as info:
            source(handler).fetch_servers()
        assert "not reachable (ConnectError)" in str(info.value)
        assert "example.test" not in str(info.value)

    def test_timeout(self):
        def handler(request):
            raise httpx.ReadTimeout("slow")
        with pytest.raises(WeblogicQueryError, match="timed out"):
            source(handler).fetch_servers()

    def test_password_not_in_repr(self):
        assert "pw-not-real" not in repr(CRED)


class TestParse:
    def test_all_running(self):
        s = parse_search(sample("domain_all_running.json"))
        fs0 = s["FoundationServices0"]
        assert (fs0.state, fs0.health, fs0.stuck_threads, fs0.heap_used_pct) == ("RUNNING", "ok", 0, 42.0)

    def test_shutdown_server_has_state_but_no_metrics(self):
        fs1 = parse_search(sample("domain_one_shutdown.json"))["FoundationServices1"]
        assert fs1.state == "SHUTDOWN"
        assert fs1.health is None and fs1.stuck_threads is None and fs1.heap_used_pct is None

    def test_stressed(self):
        fs0 = parse_search(sample("domain_stressed.json"))["FoundationServices0"]
        assert fs0.health == "warning" and fs0.stuck_threads == 2 and fs0.heap_used_pct == 95.0

    def test_missing_metric_fields_are_none(self):
        body = {"serverLifeCycleRuntimes": {"items": [{"name": "A", "state": "RUNNING"}]},
                "serverRuntimes": {"items": [{"name": "A", "state": "RUNNING"}]}}
        a = parse_search(body)["A"]
        assert a.stuck_threads is None and a.heap_used_pct is None and a.health is None

    def test_state_normalised_to_upper_health_to_lower(self):
        body = {"serverLifeCycleRuntimes": {"items": [{"name": "A", "state": "running"}]},
                "serverRuntimes": {"items": [{"name": "A", "healthState": {"state": "OK"}}]}}
        a = parse_search(body)["A"]
        assert a.state == "RUNNING" and a.health == "ok"

    @pytest.mark.parametrize("body", [
        {}, {"serverLifeCycleRuntimes": {"items": []}},
        {"serverLifeCycleRuntimes": {"items": [{"state": "RUNNING"}]}, "serverRuntimes": {"items": []}},
        {"serverLifeCycleRuntimes": {"items": [{"name": "A"}]}, "serverRuntimes": {"items": []}},
        [],
    ])
    def test_bad_shapes(self, body):
        with pytest.raises(WeblogicQueryError, match="unexpected output"):
            parse_search(body)
