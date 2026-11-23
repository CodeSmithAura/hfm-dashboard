"""pywin32 adapter: host the monitor as a Windows service, and register / control that service.

Hosting: ``dispatch`` (called by ``service_boot.py``) connects to the service manager and runs
``HfmMonitorService``, which runs ``app.service_host.run_service`` until a stop or shutdown request.
- Stop / shutdown: report STOP_PENDING with ``monitor_service.stop_timeout_seconds`` as wait hint
  and let the running poll finish; if it has not finished by then, report STOPPED and exit.
- Start-up refused (configuration problem): stop with an error, no automatic restart.
- Crash: end the process without reporting STOPPED, so Windows' recovery actions restart it.

Control (installer): ``PywinServiceControl`` implements ``ports.service_control.ServiceControl``.
Recovery actions are set with ``sc.exe failure`` (plain, documented, visible with ``sc qfailure``).
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading

import pywintypes
import servicemanager
import win32evtlogutil
import win32service
import win32serviceutil

from hfm_dashboard.app.service_host import (
    DESCRIPTION, DISPLAY_NAME, SERVICE_NAME, ServiceOptions, ServiceOutcome, load_service_rules,
    run_service,
)
from hfm_dashboard.ports.service_control import ServiceControlError, ServiceInfo, ServiceSpec

log = logging.getLogger("hfm_dashboard.service")

# Used only if rules.yaml cannot be read - then start-up is refused within seconds anyway.
FALLBACK_STOP_SECONDS = 30
ERROR_ACCESS_DENIED = 5
ERROR_SERVICE_DOES_NOT_EXIST = 1060
ERROR_NONE_MAPPED = 1332
SERVICE_LOGON_RIGHT = "SeServiceLogonRight"

_options: ServiceOptions | None = None


class HfmMonitorService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = DISPLAY_NAME
    _svc_description_ = DESCRIPTION

    def __init__(self, args) -> None:
        super().__init__(args)
        self.stop_event = threading.Event()
        self.finished = threading.Event()
        rules, _ = load_service_rules(_options) if _options else (None, None)
        self.stop_seconds = rules.monitor_service.stop_timeout_seconds if rules else FALLBACK_STOP_SECONDS

    def SvcStop(self) -> None:  # noqa: N802 - pywin32 name
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=self.stop_seconds * 1000)
        self.stop_event.set()
        threading.Thread(target=self._watchdog, name="stop-watchdog", daemon=True).start()

    SvcShutdown = SvcStop

    def _watchdog(self) -> None:
        if not self.finished.wait(self.stop_seconds):
            log.error("stop took longer than %s s; ending the process", self.stop_seconds)
            logging.shutdown()
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)
            os._exit(0)

    def SvcDoRun(self) -> None:  # noqa: N802 - pywin32 name
        assert _options is not None, "dispatch() sets the options"
        servicemanager.LogInfoMsg(f"{SERVICE_NAME}: starting; log in {_options.log_dir}")
        outcome = run_service(_options, self.stop_event)
        self.finished.set()
        if outcome is ServiceOutcome.STOPPED:
            servicemanager.LogInfoMsg(f"{SERVICE_NAME}: stopped")
            return
        if outcome is ServiceOutcome.REFUSED:
            servicemanager.LogErrorMsg(f"{SERVICE_NAME}: start-up refused - configuration problem; "
                                       f"see the log in {_options.log_dir}. Not restarted automatically.")
            raise RuntimeError("start-up refused")      # framework reports STOPPED with an error code
        servicemanager.LogErrorMsg(f"{SERVICE_NAME}: monitor crashed; see the log in {_options.log_dir}. "
                                   "Windows recovery restarts it.")
        logging.shutdown()
        os._exit(1)                                     # no STOPPED report: recovery actions apply


def dispatch(argv: list[str]) -> None:
    global _options
    _options = ServiceOptions.parse(argv)
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(HfmMonitorService)
    servicemanager.StartServiceCtrlDispatcher()


# --------------------------------------------------------------------------- control (installer)

_STATES = {
    win32service.SERVICE_STOPPED: "stopped",
    win32service.SERVICE_START_PENDING: "start_pending",
    win32service.SERVICE_STOP_PENDING: "stop_pending",
    win32service.SERVICE_RUNNING: "running",
    win32service.SERVICE_CONTINUE_PENDING: "continue_pending",
    win32service.SERVICE_PAUSE_PENDING: "pause_pending",
    win32service.SERVICE_PAUSED: "paused",
}
_START_TYPES = {
    win32service.SERVICE_AUTO_START: "auto",
    win32service.SERVICE_DEMAND_START: "manual",
    win32service.SERVICE_DISABLED: "disabled",
}


def failure_command(name: str, delays_seconds: list[int], reset_after_seconds: int) -> list[str]:
    """``sc.exe failure``: restart after each listed crash; the last delay repeats for later crashes."""
    actions = "/".join(f"restart/{d * 1000}" for d in delays_seconds)
    return ["sc.exe", "failure", name, "reset=", str(reset_after_seconds), "actions=", actions]


def _error(exc: pywintypes.error, what: str) -> ServiceControlError:
    return ServiceControlError(f"{what} failed: {exc.strerror} (Windows error {exc.winerror})",
                               access_denied=exc.winerror == ERROR_ACCESS_DENIED)


class PywinServiceControl:
    def __init__(self, run=subprocess.run) -> None:
        self._run = run

    def info(self, name: str) -> ServiceInfo | None:
        try:
            scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                h = win32service.OpenService(scm, name, win32service.SERVICE_QUERY_CONFIG
                                             | win32service.SERVICE_QUERY_STATUS)
            except pywintypes.error as exc:
                if exc.winerror == ERROR_SERVICE_DOES_NOT_EXIST:
                    return None
                raise
            try:
                cfg = win32service.QueryServiceConfig(h)
                status = win32service.QueryServiceStatus(h)
            finally:
                win32service.CloseServiceHandle(h)
            win32service.CloseServiceHandle(scm)
        except pywintypes.error as exc:
            raise _error(exc, "query service") from exc
        return ServiceInfo(state=_STATES.get(status[1], str(status[1])), command_line=cfg[3],
                           account=cfg[7], start_type=_START_TYPES.get(cfg[1], str(cfg[1])))

    def grant_service_logon(self, account: str) -> None:
        import win32security
        try:
            sid, _, _ = win32security.LookupAccountName(None, account)
        except pywintypes.error as exc:
            if exc.winerror == ERROR_NONE_MAPPED:
                raise ServiceControlError(f"account {account!r} was not found (check the DOMAIN\\name "
                                          "spelling)") from exc
            raise _error(exc, "look up account") from exc
        try:
            policy = win32security.LsaOpenPolicy(None, win32security.POLICY_CREATE_ACCOUNT
                                                 | win32security.POLICY_LOOKUP_NAMES)
            try:
                win32security.LsaAddAccountRights(policy, sid, [SERVICE_LOGON_RIGHT])
            finally:
                win32security.LsaClose(policy)
        except pywintypes.error as exc:
            raise _error(exc, "grant 'Log on as a service'") from exc

    def install(self, spec: ServiceSpec) -> None:
        try:
            win32serviceutil.InstallService(
                f"{HfmMonitorService.__module__}.{HfmMonitorService.__name__}", spec.name, spec.display_name,
                startType=win32service.SERVICE_AUTO_START, exeName=spec.command[0],
                exeArgs=subprocess.list2cmdline(spec.command[1:]), description=spec.description,
                delayedstart=spec.delayed_auto_start, userName=spec.account, password=spec.password)
            win32evtlogutil.AddSourceToRegistry(spec.name, servicemanager.__file__)
        except pywintypes.error as exc:
            raise _error(exc, "install service") from exc

    def set_recovery(self, name: str, restart_delays_seconds: list[int], reset_after_seconds: int) -> None:
        cmd = failure_command(name, restart_delays_seconds, reset_after_seconds)
        result = self._run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise ServiceControlError(f"setting recovery actions failed (sc.exe exit {result.returncode})",
                                      access_denied=result.returncode == ERROR_ACCESS_DENIED)

    def start(self, name: str) -> None:
        try:
            win32serviceutil.StartService(name)
        except pywintypes.error as exc:
            raise _error(exc, "start service") from exc

    def stop(self, name: str, timeout_seconds: int) -> None:
        try:
            win32serviceutil.StopService(name)
            win32serviceutil.WaitForServiceStatus(name, win32service.SERVICE_STOPPED, timeout_seconds)
        except pywintypes.error as exc:
            raise _error(exc, "stop service") from exc

    def remove(self, name: str) -> None:
        try:
            win32serviceutil.RemoveService(name)
        except pywintypes.error as exc:
            raise _error(exc, "remove service") from exc
        try:
            win32evtlogutil.RemoveSourceFromRegistry(name)
        except pywintypes.error:
            pass    # the event source may never have been registered
