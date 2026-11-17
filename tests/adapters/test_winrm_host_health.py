"""WinRM disk/event-log adapter: script safety, parsing, transport errors (fake pypsrp client)."""

from __future__ import annotations

import re

import pytest

from hfm_dashboard.adapters.winrm_host_health import WinRMHostHealthSource, build_script, parse_output
from hfm_dashboard.domain.environment import WinRM
from hfm_dashboard.ports.host_health import HostQueryError
from tests.adapters.test_winrm_services import Factory, FakeClient
from tests.conftest import FIXTURES

SAMPLES = FIXTURES / "winrm"
SETTINGS = WinRM(port=8443, use_ssl=False, auth="negotiate")
DB_SERVICES = ["MSSQLSERVER", "SQLSERVERAGENT"]


def sample(name):
    return (SAMPLES / name).read_text(encoding="utf-8")


class TestScript:
    FORBIDDEN = re.compile(
        r"\b(Start|Stop|Restart|Suspend|Resume|Set|New|Remove|Clear|Limit|Write)-(Service|EventLog|WinEvent|"
        r"CimInstance|Item|Content)\b|Invoke-Expression|\biex\b|Invoke-CimMethod|wevtutil|\.(Start|Stop)\(",
        re.IGNORECASE)

    def test_read_only(self):
        script = build_script(DB_SERVICES, 60, [7031, 7034], [6008, 41])
        assert {"Get-CimInstance", "Get-WinEvent", "Get-Service"} <= set(re.findall(r"Get-\w+", script))
        assert not self.FORBIDDEN.search(script)

    def test_parameters_embedded_safely(self):
        script = build_script(["A'; Stop-Service OHS; '"], 15, [7034], [6008])
        assert "'A''; Stop-Service OHS; '''" in script
        assert "$crashIds = @(7034)" in script and "$shutdownIds = @(6008)" in script
        assert "AddMinutes(-15)" in script

    def test_event_ids_must_be_integers(self):
        with pytest.raises(ValueError):
            build_script([], 60, ["7034; Remove-Item"], [])

    def test_only_system_log(self):
        assert "LogName = 'System'" in build_script([], 60, [7034], [])


class TestParse:
    def test_healthy(self):
        h = parse_output(["HyS9FinancialManagementJavaServer_EPMHFM2"], sample("host_healthy.json"))
        assert h.volumes == {"C:": 45.0, "D:": 60.0}
        assert h.service_crashes == {"HyS9FinancialManagementJavaServer_EPMHFM2": 0}
        assert h.unexpected_shutdowns == 0

    def test_problems(self):
        h = parse_output(DB_SERVICES, sample("host_problems.json"))
        assert h.volumes == {"C:": 8.6, "D:": 11.6}
        assert h.service_crashes == {"MSSQLSERVER": 0, "SQLSERVERAGENT": 2}
        assert h.unexpected_shutdowns == 1

    def test_single_items_as_objects(self):
        h = parse_output(["OHS"], sample("host_single_items.json"))
        assert h.volumes == {"C:": 45.0} and h.service_crashes == {"OHS": 0}

    def test_no_services_requested(self):
        assert parse_output([], '{"volumes": [], "crashes": [], "shutdowns": 0}').service_crashes == {}

    @pytest.mark.parametrize("output", [
        "not json", "[]", '{"volumes": [], "crashes": [], "shutdowns": "x"}',
        '{"volumes": [{"size": 1}], "crashes": [], "shutdowns": 0}',
    ])
    def test_bad_shapes(self, output):
        with pytest.raises(HostQueryError, match="unexpected output"):
            parse_output([], output)

    def test_missing_service_result(self):
        with pytest.raises(HostQueryError, match="no event result for 1 service"):
            parse_output(DB_SERVICES, '{"volumes": [], "crashes": [{"wanted": "MSSQLSERVER", "crashes": 0}], "shutdowns": 0}')


class TestTransport:
    def test_query_runs_script_and_parses(self):
        factory = Factory(FakeClient(sample("host_problems.json")))
        h = WinRMHostHealthSource(SETTINGS, 20, client_factory=factory).query(
            "db-node-01.example.test", DB_SERVICES, 60, [7034], [6008])
        assert h.unexpected_shutdowns == 1
        assert "Get-WinEvent" in factory.client.scripts[0]
        assert factory.calls[0][1]["operation_timeout"] == 20

    def test_transport_error_mapped(self):
        factory = Factory(FakeClient(raises=ConnectionError("refused db-node-01.example.test")))
        with pytest.raises(HostQueryError) as info:
            WinRMHostHealthSource(SETTINGS, 20, client_factory=factory).query("h", [], 60, [7034], [])
        assert "WinRM not reachable" in str(info.value) and "example.test" not in str(info.value)

    def test_host_error_stream(self):
        factory = Factory(FakeClient("{}", had_errors=True))
        with pytest.raises(HostQueryError, match="host query reported an error"):
            WinRMHostHealthSource(SETTINGS, 20, client_factory=factory).query("h", [], 60, [7034], [])
