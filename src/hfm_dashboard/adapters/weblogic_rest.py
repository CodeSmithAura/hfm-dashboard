"""WebLogic REST management API (12.2.1.x) on the Admin Server. Read-only.

One bulk-access search request returns every server's lifecycle state and, for running servers,
health, stuck-thread count and heap. The search endpoint takes a POST body but only reads; no
other endpoint is called (hard rule 3). Authentication: HTTP basic with the read-only Monitor user
(A09) from the secret store.
"""

from __future__ import annotations

from typing import Any

import httpx

from hfm_dashboard.adapters.tls import verify_setting
from hfm_dashboard.domain.evaluation import WeblogicObservation
from hfm_dashboard.ports.secrets import Secret
from hfm_dashboard.ports.weblogic import WeblogicQueryError

SEARCH_PATH = "/management/weblogic/latest/domainRuntime/search"

SEARCH_QUERY: dict[str, Any] = {
    "links": [],
    "fields": [],
    "children": {
        "serverLifeCycleRuntimes": {"links": [], "fields": ["name", "state"]},
        "serverRuntimes": {
            "links": [],
            "fields": ["name", "state", "healthState"],
            "children": {
                "threadPoolRuntime": {"links": [], "fields": ["stuckThreadCount"]},
                "JVMRuntime": {"links": [], "fields": ["heapFreePercent"]},
            },
        },
    },
}


class WeblogicRestSource:
    def __init__(
        self,
        admin_url: str,
        credential: Secret,
        timeout_seconds: float,
        cert_validation: bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._admin_url = admin_url.rstrip("/")
        self._credential = credential
        self._timeout = timeout_seconds
        self._verify = cert_validation
        self._transport = transport   # tests pass httpx.MockTransport

    def fetch_servers(self) -> dict[str, WeblogicObservation]:
        try:
            with httpx.Client(
                base_url=self._admin_url,
                auth=(self._credential.username, self._credential.password),
                timeout=self._timeout,
                verify=verify_setting(self._verify),
                transport=self._transport,
                headers={"Accept": "application/json", "X-Requested-By": "hfm-dashboard"},
            ) as client:
                response = client.post(SEARCH_PATH, json=SEARCH_QUERY)
        except httpx.TimeoutException as exc:
            raise WeblogicQueryError("WebLogic REST timed out") from exc
        except httpx.HTTPError as exc:
            raise WeblogicQueryError(f"WebLogic REST not reachable ({type(exc).__name__})") from exc

        if response.status_code == 401:
            raise WeblogicQueryError("WebLogic authentication failed")
        if response.status_code == 403:
            raise WeblogicQueryError("WebLogic refused the request (Monitor role?)")
        if response.status_code != 200:
            raise WeblogicQueryError(f"WebLogic REST returned HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise WeblogicQueryError("unexpected output from WebLogic REST (not JSON)") from exc
        return parse_search(body)


def _items(body: Any, key: str) -> list[dict[str, Any]]:
    section = body.get(key) if isinstance(body, dict) else None
    items = section.get("items") if isinstance(section, dict) else None
    if not isinstance(items, list) or not all(isinstance(i, dict) and isinstance(i.get("name"), str)
                                              for i in items):
        raise WeblogicQueryError(f"unexpected output from WebLogic REST ({key})")
    return items


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_search(body: Any) -> dict[str, WeblogicObservation]:
    lifecycle = {i["name"]: i.get("state") for i in _items(body, "serverLifeCycleRuntimes")}
    runtimes = {i["name"]: i for i in _items(body, "serverRuntimes")}

    result: dict[str, WeblogicObservation] = {}
    for name in sorted(set(lifecycle) | set(runtimes)):
        rt = runtimes.get(name, {})
        state = lifecycle.get(name) or rt.get("state")
        if not isinstance(state, str):
            raise WeblogicQueryError(f"unexpected output from WebLogic REST (no state for a server)")
        health = rt.get("healthState")
        health_state = health.get("state") if isinstance(health, dict) else None
        stuck = _number((rt.get("threadPoolRuntime") or {}).get("stuckThreadCount"))
        heap_free = _number((rt.get("JVMRuntime") or {}).get("heapFreePercent"))
        result[name] = WeblogicObservation(
            state=state.upper(),
            stuck_threads=int(stuck) if stuck is not None else None,
            heap_used_pct=100.0 - heap_free if heap_free is not None else None,
            health=health_state.lower() if isinstance(health_state, str) else None,
        )
    return result
