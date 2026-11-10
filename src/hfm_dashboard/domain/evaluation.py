"""Check evaluation: raw observations from collectors -> Up / Degraded / Down / Unknown.

Collectors only report what they saw (a service state string, an HTTP status and response time,
WebLogic runtime values, SQL Server metrics). This module applies ``rules.checks`` to turn an
observation into a status with a short human-readable reason. Pure domain code: no I/O, no clock.

Conventions (docs/decisions.md D17):
- A metric the collector could not read is ``None``. It contributes Unknown, so a missing metric
  never passes as healthy; a definite Degraded/Down from another metric still wins (D13).
- A state string that rules.yaml does not list under up/degraded/down is Unknown: the check ran,
  but the result cannot be classified, and the rules need updating.
- Thresholds named ``..._above`` / ``..._longer_than`` are strict (>), ``..._below`` is strict (<).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from hfm_dashboard.domain.rules import Checks, StateMapCheck
from hfm_dashboard.domain.status import Status, worst


# --------------------------------------------------------------------------- observations

@dataclass(frozen=True, slots=True)
class CollectionFailure:
    """The collector could not run the check at all (no access, collector timeout, ...)."""

    reason: str


@dataclass(frozen=True, slots=True)
class ServiceObservation:
    """Windows service state as reported by the service control manager, or ``NotFound``."""

    state: str


@dataclass(frozen=True, slots=True)
class UrlProbeObservation:
    """One HTTP request. ``http_status`` is None when no response arrived."""

    http_status: int | None
    elapsed_seconds: float | None
    timed_out: bool = False
    error: str = ""          # e.g. "connection refused"; never a URL with credentials


@dataclass(frozen=True, slots=True)
class WeblogicObservation:
    """Server runtime from the WebLogic REST API."""

    state: str
    stuck_threads: int | None = None
    heap_used_pct: float | None = None


@dataclass(frozen=True, slots=True)
class SqlServerObservation:
    """SQL Server health from the service state and DMVs / msdb.

    Failure lists hold the age in hours of each failure the collector found; an empty tuple means
    none were found, ``None`` means the collector could not read them.
    """

    service_running: bool | None
    longest_blocking_minutes: float | None = None
    deadlocks_last_15_min: int | None = None
    max_log_used_pct: float | None = None
    tempdb_used_pct: float | None = None
    disk_free_pct: dict[str, float] | None = None       # drive/volume label -> % free
    backup_failures_hours_ago: tuple[float, ...] | None = None
    agent_job_failures_hours_ago: tuple[float, ...] | None = None
    longest_query_minutes: float | None = None


Observation = Union[
    CollectionFailure, ServiceObservation, UrlProbeObservation, WeblogicObservation,
    SqlServerObservation,
]


@dataclass(frozen=True, slots=True)
class Evaluation:
    status: Status
    detail: str


# --------------------------------------------------------------------------- evaluation

def evaluate(check_ref: str, observation: Observation, checks: Checks) -> Evaluation:
    """Evaluate one observation for a component check reference such as ``url_probe:hfm_web``."""
    kind, _, arg = check_ref.partition(":")
    if isinstance(observation, CollectionFailure):
        return Evaluation(Status.UNKNOWN, f"check could not run: {observation.reason}")

    expected = _OBSERVATION_FOR.get(kind)
    if expected is None:
        raise ValueError(f"unknown check type {kind!r}")
    if not isinstance(observation, expected):
        raise TypeError(f"{check_ref} expects {expected.__name__}, got {type(observation).__name__}")

    if kind == "windows_service":
        return _state(checks.windows_service, observation.state, "service")
    if kind == "url_probe":
        return _url_probe(arg, observation, checks)
    if kind == "weblogic_server":
        return _weblogic(observation, checks)
    return _sql_server(observation, checks)


_OBSERVATION_FOR: dict[str, type] = {
    "windows_service": ServiceObservation,
    "url_probe": UrlProbeObservation,
    "weblogic_server": WeblogicObservation,
    "sql_server": SqlServerObservation,
}


def _combine(findings: list[tuple[Status, str]]) -> Evaluation:
    """Worst status wins; the detail lists every non-Up finding (or the Up summary)."""
    status = worst([s for s, _ in findings])
    reasons = [d for s, d in findings if s is not Status.UP]
    if not reasons:
        reasons = [d for _, d in findings]
    return Evaluation(status, "; ".join(reasons))


def _state(rule: StateMapCheck, state: str, label: str) -> Evaluation:
    if state in rule.up:
        return Evaluation(Status.UP, f"{label} {state}")
    if state in rule.degraded:
        return Evaluation(Status.DEGRADED, f"{label} {state}")
    if state in rule.down:
        return Evaluation(Status.DOWN, f"{label} {state}")
    return Evaluation(Status.UNKNOWN, f"{label} state {state!r} not listed in rules.yaml")


def _url_probe(probe: str, obs: UrlProbeObservation, checks: Checks) -> Evaluation:
    rule = checks.url_probe
    if obs.timed_out or (obs.elapsed_seconds is not None and obs.elapsed_seconds >= rule.timeout_seconds):
        return Evaluation(Status.DOWN, f"no response within {rule.timeout_seconds:g} s")
    if obs.http_status is None:
        return Evaluation(Status.DOWN, f"no response: {obs.error or 'request failed'}")
    if obs.http_status in rule.down_on_http_status:
        return Evaluation(Status.DOWN, f"HTTP {obs.http_status}")

    findings: list[tuple[Status, str]] = []
    if obs.http_status in rule.up_on_http_status:
        findings.append((Status.UP, f"HTTP {obs.http_status}"))
    else:
        findings.append((Status.DEGRADED, f"unexpected HTTP {obs.http_status}"))

    limit = rule.degraded_after_seconds[probe]
    if obs.elapsed_seconds is None:
        findings.append((Status.UNKNOWN, "response time not measured"))
    elif obs.elapsed_seconds > limit:
        findings.append((Status.DEGRADED, f"slow: {obs.elapsed_seconds:.1f} s > {limit:g} s"))
    else:
        findings.append((Status.UP, f"{obs.elapsed_seconds:.1f} s"))
    return _combine(findings)


def _weblogic(obs: WeblogicObservation, checks: Checks) -> Evaluation:
    rule = checks.weblogic_server
    state = _state(rule, obs.state, "server")
    if state.status is Status.DOWN:
        return state  # runtime metrics of a dead server are meaningless

    findings = [(state.status, state.detail)]
    if obs.stuck_threads is None:
        findings.append((Status.UNKNOWN, "stuck threads not read"))
    elif obs.stuck_threads > rule.degraded_if_stuck_threads_above:
        findings.append((Status.DEGRADED, f"{obs.stuck_threads} stuck thread(s)"))
    if obs.heap_used_pct is None:
        findings.append((Status.UNKNOWN, "heap not read"))
    elif obs.heap_used_pct > rule.degraded_if_heap_used_pct_above:
        findings.append((Status.DEGRADED,
                         f"heap {obs.heap_used_pct:.1f}% > {rule.degraded_if_heap_used_pct_above:g}%"))
    return _combine(findings)


def _sql_server(obs: SqlServerObservation, checks: Checks) -> Evaluation:
    rule = checks.sql_server
    findings: list[tuple[Status, str]] = []

    if rule.down_if_service_not_running:
        if obs.service_running is False:
            return Evaluation(Status.DOWN, "SQL Server service not running")
        if obs.service_running is None:
            findings.append((Status.UNKNOWN, "service state not read"))

    def above(value: float | None, limit: float, what: str, unit: str) -> None:
        if value is None:
            findings.append((Status.UNKNOWN, f"{what} not read"))
        elif value > limit:
            findings.append((Status.DEGRADED, f"{what} {value:g}{unit} > {limit:g}{unit}"))

    above(obs.longest_blocking_minutes, rule.degraded_if_blocking_longer_than_minutes,
          "blocking", " min")
    above(obs.deadlocks_last_15_min, rule.degraded_if_deadlocks_in_15_min_above,
          "deadlocks in 15 min", "")
    above(obs.max_log_used_pct, rule.degraded_if_log_used_pct_above, "log used", "%")
    above(obs.tempdb_used_pct, rule.degraded_if_tempdb_used_pct_above, "tempdb used", "%")
    above(obs.longest_query_minutes, rule.degraded_if_query_running_longer_than_minutes,
          "longest query", " min")

    if obs.disk_free_pct is None:
        findings.append((Status.UNKNOWN, "disk space not read"))
    else:
        for drive, free in sorted(obs.disk_free_pct.items()):
            if free < rule.down_if_disk_free_pct_below:
                findings.append((Status.DOWN, f"{drive} {free:g}% free < {rule.down_if_disk_free_pct_below:g}%"))
            elif free < rule.degraded_if_disk_free_pct_below:
                findings.append((Status.DEGRADED,
                                 f"{drive} {free:g}% free < {rule.degraded_if_disk_free_pct_below:g}%"))

    def recent(ages: tuple[float, ...] | None, window: float, what: str) -> None:
        if ages is None:
            findings.append((Status.UNKNOWN, f"{what} history not read"))
            return
        count = sum(1 for age in ages if 0 <= age <= window)
        if count:
            findings.append((Status.DEGRADED, f"{count} failed {what}(s) in last {window:g} h"))

    recent(obs.backup_failures_hours_ago, rule.degraded_if_backup_failed_in_last_hours, "backup")
    recent(obs.agent_job_failures_hours_ago, rule.degraded_if_agent_job_failed_in_last_hours,
           "Agent job")

    if not findings:
        findings.append((Status.UP, "all SQL Server checks within thresholds"))
    return _combine(findings)
