"""WinRM services adapter: script safety, output parsing, transport error mapping.

No real WinRM connection is made: a fake client factory stands in for pypsrp's Client.
"""

from __future__ import annotations

import re

import pytest
from pypsrp import exceptions as psrp_exc

from hfm_dashboard.adapters.fake_services import FakeWindowsServiceSource
from hfm_dashboard.adapters.winrm_services import (
    Credential,
    WinRMServiceSource,
    build_script,
    parse_output,
)
from hfm_dashboard.domain.environment import WinRM
from hfm_dashboard.ports.windows_services import ServiceQueryError
from tests.conftest import FIXTURES

SAMPLES = FIXTURES / "winrm"
WEB1 = ["OHS", "Oracle Weblogic ohs NodeManager (D_Oracle_Middleware_ohs_wlserver)",
        "Oracle Weblogic EPMSystem_AdminServer", "HyS9FoundationServices_EPMWEB1",
        "HyS9FinancialManagementWeb_EPMWEB1", "HyS9FRReports_EPMWEB1"]
WEB2 = ["OHS", "Oracle Weblogic ohs NodeManager (D_Oracle_Middleware_ohs_wlserver)",
        "HyS9FoundationServices_EPMWEB2", "HyS9FinancialManagementWeb_EPMWEB2",
        "HyS9FRReports_EPMWEB2"]
SETTINGS = WinRM(port=8443, use_ssl=True, auth="kerberos")


def sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- script

class TestScript:
    # Any verb that changes state is forbidden (hard rule 3: read-only against HFM)
    FORBIDDEN = re.compile(
        r"\b(Start|Stop|Restart|Suspend|Resume|Set|New|Remove)-Service\b|\bsc(\.exe)?\s|"
        r"Invoke-Expression|\biex\b|\.(Start|Stop|Pause|Continue)\(",
        re.IGNORECASE,
    )

    def test_read_only(self):
        script = build_script(WEB1)
        assert "Get-Service" in script
        assert not self.FORBIDDEN.search(script)

    def test_names_are_single_quoted_literals(self):
        script = build_script(["A (x)", "B$env:USERNAME", "C'; Stop-Service OHS; '"])
        assert "'A (x)'" in script
        assert "'B$env:USERNAME'" in script          # single quotes: no variable expansion
        assert "'C''; Stop-Service OHS; '''" in script  # embedded quote doubled, stays one literal

    def test_matches_name_then_display_name(self):
        script = build_script(["X"])
        assert script.index("$_.Name -eq $w") < script.index("$_.DisplayName -eq $w")

    def test_output_is_an_array(self):
        assert "ConvertTo-Json -InputObject @($out) -Compress" in build_script(["X"])


# --------------------------------------------------------------------------- parsing

class TestParse:
    def test_all_running(self):
        states = parse_output(WEB1, sample("web_node_all_running.json"))
        assert list(states) == WEB1
        assert set(states.values()) == {"Running"}

    def test_mixed(self):
        states = parse_output(WEB2, sample("web_node_mixed.json"))
        assert states["HyS9FRReports_EPMWEB2"] == "NotFound"
        assert states["HyS9FoundationServices_EPMWEB2"] == "StartPending"
        assert states["Oracle Weblogic ohs NodeManager (D_Oracle_Middleware_ohs_wlserver)"] == "Stopped"

    def test_single_object(self):
        assert parse_output(["MSSQLSERVER"], sample("single_object.json")) == {"MSSQLSERVER": "Running"}

    def test_returns_only_requested_in_request_order(self):
        states = parse_output(["HyS9FRReports_EPMWEB1", "OHS"], sample("web_node_all_running.json"))
        assert list(states) == ["HyS9FRReports_EPMWEB1", "OHS"]

    def test_not_json(self):
        with pytest.raises(ServiceQueryError, match="not JSON"):
            parse_output(["OHS"], sample("not_json.txt"))

    def test_missing_result(self):
        with pytest.raises(ServiceQueryError, match="no result for 1 service"):
            parse_output(["OHS", "MSSQLSERVER"], '[{"wanted":"OHS","status":"Running"}]')

    @pytest.mark.parametrize("output", ['"text"', '[1, 2]', '[{"wanted": "OHS"}]', ""])
    def test_bad_shapes(self, output):
        with pytest.raises(ServiceQueryError):
            parse_output(["OHS"], output)


# --------------------------------------------------------------------------- transport

class FakeClient:
    def __init__(self, output="", had_errors=False, raises=None):
        self.output, self.had_errors, self.raises = output, had_errors, raises
        self.scripts: list[str] = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def execute_ps(self, script):
        self.scripts.append(script)
        if self.raises:
            raise self.raises
        return self.output, None, self.had_errors


class Factory:
    def __init__(self, client):
        self.client = client
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, host, **kwargs):
        self.calls.append((host, kwargs))
        return self.client


class TestWinRMSource:
    def test_query_passes_settings_and_parses(self):
        factory = Factory(FakeClient(sample("web_node_all_running.json")))
        src = WinRMServiceSource(SETTINGS, timeout_seconds=20, client_factory=factory)
        states = src.query("web-node-01.example.test", WEB1)
        assert set(states.values()) == {"Running"}
        host, kwargs = factory.calls[0]
        assert host == "web-node-01.example.test"
        assert kwargs["port"] == 8443 and kwargs["ssl"] is True and kwargs["auth"] == "kerberos"
        assert kwargs["connection_timeout"] == 20 and kwargs["operation_timeout"] == 20
        assert kwargs["read_timeout"] > kwargs["operation_timeout"]
        assert "username" not in kwargs and "password" not in kwargs   # gMSA identity, no secret
        assert factory.client.closed

    def test_real_pypsrp_client_accepts_settings(self):
        """Builds pypsrp's Client offline (no connection is made until a command runs)."""
        from hfm_dashboard.adapters.winrm_services import _default_client_factory

        plain = WinRM(port=8443, use_ssl=False, auth="negotiate")
        src = WinRMServiceSource(plain, timeout_seconds=20)
        client = _default_client_factory("web-node-01.example.test", **src._client_kwargs())
        assert client.wsman.transport.endpoint == "http://web-node-01.example.test:8443/wsman"
        assert client.wsman.operation_timeout == 20
        assert client.wsman.transport.read_timeout > 20

    def test_explicit_credential_passed_but_hidden_in_repr(self):
        cred = Credential("dev-user", "s3cret-value")
        factory = Factory(FakeClient(sample("single_object.json")))
        WinRMServiceSource(SETTINGS, 20, credential=cred, client_factory=factory).query("h", ["MSSQLSERVER"])
        assert factory.calls[0][1]["password"] == "s3cret-value"
        assert "s3cret-value" not in repr(cred)

    def test_empty_request_makes_no_connection(self):
        factory = Factory(FakeClient())
        assert WinRMServiceSource(SETTINGS, 20, client_factory=factory).query("h", []) == {}
        assert factory.calls == []

    def test_had_errors(self):
        src = WinRMServiceSource(SETTINGS, 20, client_factory=Factory(FakeClient("[]", had_errors=True)))
        with pytest.raises(ServiceQueryError, match="reported an error"):
            src.query("h", ["OHS"])

    @pytest.mark.parametrize(("exc", "expected"), [
        (psrp_exc.AuthenticationError("user dev-user rejected"), "authentication failed"),
        (psrp_exc.WinRMTransportError("http", 500, "boom"), "not reachable"),
        (ConnectionError("refused"), "not reachable"),
        (TimeoutError("slow"), "timed out"),
        (RuntimeError("odd"), "query failed (RuntimeError)"),
    ])
    def test_transport_errors_mapped_without_exception_text(self, exc, expected):
        src = WinRMServiceSource(SETTINGS, 20, client_factory=Factory(FakeClient(raises=exc)))
        with pytest.raises(ServiceQueryError) as info:
            src.query("h", ["OHS"])
        assert expected in str(info.value)
        assert "dev-user" not in str(info.value)

    def test_factory_failure_mapped(self):
        def broken(host, **kwargs):
            raise ConnectionError("dns")
        with pytest.raises(ServiceQueryError, match="not reachable"):
            WinRMServiceSource(SETTINGS, 20, client_factory=broken).query("h", ["OHS"])


# --------------------------------------------------------------------------- fake

class TestFake:
    def test_states_notfound_failures_and_calls(self):
        fake = FakeWindowsServiceSource({"a": {"OHS": "Running"}}, failures={"b": "access denied"})
        assert fake.query("a", ["OHS", "X"]) == {"OHS": "Running", "X": "NotFound"}
        with pytest.raises(ServiceQueryError, match="access denied"):
            fake.query("b", ["OHS"])
        assert fake.calls == [("a", ("OHS", "X")), ("b", ("OHS",))]
