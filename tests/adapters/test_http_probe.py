"""HTTP URL prober against httpx.MockTransport (no network)."""

from __future__ import annotations

import socket
import ssl

import httpx
import pytest

from hfm_dashboard.adapters.http_probe import HttpxUrlProber
from hfm_dashboard.adapters.tls import verify_setting

URL = "http://web-node-01.example.test:8443/hfmadf/"


class Clock:
    """Each call advances by ``step`` seconds."""

    def __init__(self, step: float) -> None:
        self.t, self.step = 0.0, step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def prober(handler, step=0.25, **kw) -> HttpxUrlProber:
    return HttpxUrlProber(60, transport=httpx.MockTransport(handler), clock=Clock(step), **kw)


class TestProbe:
    def test_ok_with_elapsed_time(self):
        obs = prober(lambda r: httpx.Response(200, text="<html/>"), step=1.5).probe(URL)
        assert obs.http_status == 200 and obs.elapsed_seconds == pytest.approx(1.5)
        assert not obs.timed_out and obs.error == ""

    def test_plain_get_without_credentials(self):
        seen: list[httpx.Request] = []
        prober(lambda r: seen.append(r) or httpx.Response(200)).probe(URL)
        (req,) = seen
        assert req.method == "GET" and str(req.url) == URL
        assert "authorization" not in {k.lower() for k in req.headers}
        assert "cookie" not in {k.lower() for k in req.headers}

    def test_redirect_not_followed(self):
        seen: list[str] = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://sso.example.test/login"})

        obs = prober(handler).probe(URL)
        assert obs.http_status == 302 and seen == [URL]

    def test_error_status_reported(self):
        assert prober(lambda r: httpx.Response(503)).probe(URL).http_status == 503

    def test_timeout(self):
        def handler(request):
            raise httpx.ReadTimeout("slow")
        obs = prober(handler).probe(URL)
        assert obs.timed_out and obs.http_status is None

    def test_read_timeout_is_down_side(self):
        def handler(request):
            raise httpx.ReadTimeout("slow")
        obs = prober(handler).probe(URL)
        assert obs.timed_out and not obs.unreachable

    def test_connect_timeout_is_unreachable(self):
        def handler(request):
            raise httpx.ConnectTimeout("no SYN-ACK")
        obs = prober(handler).probe(URL)
        assert obs.unreachable and not obs.timed_out and "connect timeout" in obs.error


def _connect_error(cause):
    try:
        raise httpx.ConnectError("[e] while connecting to web-node-01.example.test") from cause
    except httpx.ConnectError as exc:
        return exc


class TestClassification:
    @pytest.mark.parametrize(("exc", "reason", "unreachable"), [
        (_connect_error(ConnectionRefusedError(10061, "actively refused")), "connection refused", False),
        (_connect_error(OSError(111, "Connection refused")), "connection refused", False),
        (_connect_error(socket.gaierror(11001, "getaddrinfo failed")), "could not be resolved", True),
        (_connect_error(OSError(10065, "host unreachable")), "network or host unreachable", True),
        (_connect_error(OSError(10051, "network unreachable")), "network or host unreachable", True),
        (_connect_error(ssl.SSLCertVerificationError("certificate verify failed")), "TLS / certificate", True),
        (httpx.ConnectError("refused by web-node-01.example.test"), "connection refused", False),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]"), "TLS / certificate", True),
        (httpx.ConnectError("something odd"), "could not connect", True),
        (httpx.RemoteProtocolError("peer closed web-node-01.example.test"), "closed the connection", False),
        (httpx.ProxyError("proxy web-node-01.example.test"), "proxy error", True),
    ])
    def test_failures(self, exc, reason, unreachable):
        def handler(request):
            raise exc
        obs = prober(handler).probe(URL)
        assert obs.http_status is None and not obs.timed_out
        assert reason in obs.error and obs.unreachable is unreachable
        assert "example.test" not in obs.error


class TestTls:
    def test_validation_uses_os_trust_store(self):
        ctx = verify_setting(True)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_validation_off(self):
        assert verify_setting(False) is False
