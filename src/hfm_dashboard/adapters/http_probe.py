"""URL probes over HTTP(S) with httpx. Read-only: a plain GET, no credentials, no cookies kept.

Redirects are not followed: the first response is what is measured and judged, so a redirect to a
sign-on page on another host is neither counted as healthy content nor as slow. The full body is
read so the elapsed time covers the whole response.

Failures (decision D26):
- refused connection, read timeout, server dropping the connection -> the application side failed
  (Down after evaluation);
- DNS failure, network / host unreachable, connect timeout, TLS or proxy problems -> the host could
  not be reached from the monitoring server (``unreachable``, Unknown after evaluation).
"""

from __future__ import annotations

import errno
import socket
import ssl
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
        except httpx.ConnectTimeout:
            return UrlProbeObservation(http_status=None, elapsed_seconds=None, unreachable=True,
                                       error="no answer to the connection attempt (connect timeout)")
        except httpx.TimeoutException:
            return UrlProbeObservation(http_status=None, elapsed_seconds=None, timed_out=True)
        except httpx.HTTPError as exc:
            reason, unreachable = classify(exc)
            return UrlProbeObservation(http_status=None, elapsed_seconds=self._clock() - start,
                                       error=reason, unreachable=unreachable)
        return UrlProbeObservation(http_status=response.status_code, elapsed_seconds=self._clock() - start)


_UNREACHABLE_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.EHOSTDOWN,
                       10050, 10051, 10064, 10065}            # Windows WSAENETDOWN/UNREACH, HOSTDOWN/UNREACH
_REFUSED_ERRNOS = {errno.ECONNREFUSED, 10061}                # WSAECONNREFUSED


def _causes(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def classify(exc: httpx.HTTPError) -> tuple[str, bool]:
    """(short reason without the URL, unreachable?) for a failed request."""
    if isinstance(exc, httpx.RemoteProtocolError):
        return "server closed the connection or sent an invalid response", False
    if isinstance(exc, httpx.ProxyError):
        return "proxy error", True
    if not isinstance(exc, httpx.ConnectError):
        return f"request failed ({type(exc).__name__})", False

    for cause in _causes(exc):
        if isinstance(cause, ConnectionRefusedError) or getattr(cause, "errno", None) in _REFUSED_ERRNOS:
            return "connection refused (nothing listening on the port)", False
        if isinstance(cause, socket.gaierror):
            return "host name could not be resolved", True
        if isinstance(cause, ssl.SSLError):
            return "TLS / certificate problem", True
        if getattr(cause, "errno", None) in _UNREACHABLE_ERRNOS:
            return "network or host unreachable", True

    text = " ".join(str(c) for c in _causes(exc)).lower()    # no cause chain: fall back to the message
    if "refused" in text or "10061" in text:
        return "connection refused (nothing listening on the port)", False
    if "certificate" in text or "ssl" in text or "tls" in text:
        return "TLS / certificate problem", True
    if "getaddrinfo" in text or "name or service not known" in text or "nodename" in text:
        return "host name could not be resolved", True
    return "could not connect (host not reachable)", True
