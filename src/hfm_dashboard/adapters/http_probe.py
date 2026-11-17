"""URL probes over HTTP(S) with httpx. Read-only: a plain GET, no credentials, no cookies kept.

Redirects are not followed: the first response is what is measured and judged, so a redirect to a
sign-on page on another host is neither counted as healthy content nor as slow. The full body is
read so the elapsed time covers the whole response.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from hfm_dashboard.adapters.tls import verify_setting
from hfm_dashboard.domain.evaluation import UrlProbeObservation


class HttpxUrlProber:
    def __init__(
        self,
        timeout_seconds: float,
        cert_validation: bool = True,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout = timeout_seconds
        self._verify = cert_validation
        self._transport = transport   # tests pass httpx.MockTransport
        self._clock = clock

    def probe(self, url: str) -> UrlProbeObservation:
        start = self._clock()
        try:
            with httpx.Client(
                timeout=self._timeout,
                verify=verify_setting(self._verify),
                follow_redirects=False,
                transport=self._transport,
                headers={"User-Agent": "hfm-availability-monitor"},
            ) as client:
                response = client.get(url)
                response.read()
        except httpx.TimeoutException:
            return UrlProbeObservation(http_status=None, elapsed_seconds=None, timed_out=True)
        except httpx.HTTPError as exc:
            return UrlProbeObservation(http_status=None, elapsed_seconds=self._clock() - start,
                                       error=_describe(exc))
        return UrlProbeObservation(http_status=response.status_code, elapsed_seconds=self._clock() - start)


def _describe(exc: httpx.HTTPError) -> str:
    """Short reason without the URL (exception text usually contains it)."""
    if isinstance(exc, httpx.ConnectError):
        return "connection failed (refused, unreachable or TLS/certificate problem)"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "server closed the connection or sent an invalid response"
    return f"request failed ({type(exc).__name__})"
