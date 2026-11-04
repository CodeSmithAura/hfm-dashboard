"""Status model: Up / Degraded / Down / Unknown for checks, nodes and components.

Pure domain code: no I/O, no clock. Callers pass UTC timestamps in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Status(StrEnum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"  # the check could not run (no access, timeout of the collector itself, ...)


# Precedence when several checks on one node disagree: the worst definite result wins.
# Unknown ranks below Degraded/Down (a definite failure is more informative than "could not
# check") but above Up (one unrunnable check means we cannot claim the node is fully Up).
# Basis: ASSUMED Q53.
_SEVERITY = {Status.UP: 0, Status.UNKNOWN: 1, Status.DEGRADED: 2, Status.DOWN: 3}


def worst(statuses: list[Status]) -> Status:
    """Combine the results of several checks on one node into one raw node status."""
    if not statuses:
        return Status.UNKNOWN
    return max(statuses, key=_SEVERITY.__getitem__)


def _require_utc(ts: datetime) -> None:
    if ts.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of one check on one node in one poll, already mapped to a Status by a collector."""

    component: str
    node: str
    check: str            # e.g. "windows_service", "url_probe:hfm_web"
    status: Status
    observed_at: datetime  # UTC
    detail: str = ""       # human-readable reason; never a secret or connection string

    def __post_init__(self) -> None:
        _require_utc(self.observed_at)


@dataclass(frozen=True, slots=True)
class NodeState:
    """Confirmed status of one component on one node, with flap protection.

    ``status`` is what scoring and notices use. A raw Down only becomes a confirmed Down after
    ``confirm_down_after_consecutive_failures`` consecutive failed polls; until then the node
    keeps its previous confirmed status.
    """

    component: str
    node: str
    status: Status = Status.UNKNOWN
    consecutive_failures: int = 0
    since: datetime | None = None   # UTC time the confirmed status last changed
    last_raw: Status = Status.UNKNOWN
    detail: str = ""


def advance(
    previous: NodeState,
    raw: Status,
    observed_at: datetime,
    confirm_down_after: int,
    detail: str = "",
) -> NodeState:
    """Fold one poll's raw node status into the confirmed state (pure reducer)."""
    _require_utc(observed_at)
    if confirm_down_after < 1:
        raise ValueError("confirm_down_after must be >= 1")

    if raw is Status.DOWN:
        failures = previous.consecutive_failures + 1
        new_status = Status.DOWN if failures >= confirm_down_after else previous.status
    else:
        failures = 0
        new_status = raw

    since = previous.since if new_status is previous.status and previous.since else observed_at
    return NodeState(
        component=previous.component,
        node=previous.node,
        status=new_status,
        consecutive_failures=failures,
        since=since,
        last_raw=raw,
        detail=detail,
    )
