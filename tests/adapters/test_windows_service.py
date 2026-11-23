"""pywin32 adapter: service class shape and the recovery command (no service manager calls)."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="pywin32 is Windows-only")


def test_service_class_accepts_stop_and_shutdown():
    import win32service

    from hfm_dashboard.adapters.windows_service import HfmMonitorService
    from hfm_dashboard.app.service_host import SERVICE_NAME

    assert HfmMonitorService._svc_name_ == SERVICE_NAME
    accepted = HfmMonitorService.GetAcceptedControls(HfmMonitorService.__new__(HfmMonitorService))
    assert accepted & win32service.SERVICE_ACCEPT_STOP and accepted & win32service.SERVICE_ACCEPT_SHUTDOWN
    assert not accepted & win32service.SERVICE_ACCEPT_PAUSE_CONTINUE


def test_failure_command():
    from hfm_dashboard.adapters.windows_service import failure_command

    assert failure_command("Svc", [60, 120, 300], 86400) == [
        "sc.exe", "failure", "Svc", "reset=", "86400", "actions=", "restart/60000/restart/120000/restart/300000"]


def test_set_recovery_reports_sc_failure():
    from hfm_dashboard.adapters.windows_service import PywinServiceControl
    from hfm_dashboard.ports.service_control import ServiceControlError

    class Result:
        returncode, stdout, stderr = 5, "", "Access is denied."

    calls = []
    control = PywinServiceControl(run=lambda cmd, **kw: calls.append(cmd) or Result())
    with pytest.raises(ServiceControlError) as err:
        control.set_recovery("Svc", [60], 3600)
    assert err.value.access_denied and calls[0][:3] == ["sc.exe", "failure", "Svc"]


def test_query_of_missing_service_is_none():
    from hfm_dashboard.adapters.windows_service import PywinServiceControl

    assert PywinServiceControl().info("HfmMonitorTestNoSuchService") is None


def test_grant_to_unknown_account_names_the_problem():
    from hfm_dashboard.adapters.windows_service import PywinServiceControl
    from hfm_dashboard.ports.service_control import ServiceControlError

    with pytest.raises(ServiceControlError, match="was not found"):
        PywinServiceControl().grant_service_logon("hfm-monitor-no-such-account-test")
