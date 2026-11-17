"""Caution notice engine: a pure state machine per component (no I/O, no clock, no recipients).

Per poll and environment, ``evaluate_notices`` takes the confirmed node states, the component
statuses from scoring, the active maintenance windows and the previous notice states, and returns
new notice states plus the notices to send. Every timing comes from rules.yaml (decision D28):

    failure starts --first_notice_after_minutes (close period: ..._close)--> FIRST
    still failing  --every repeat_every_minutes--------------------------->  REMINDER
    still failing  --escalate_to_manager_after_minutes (once)------------->  ESCALATION
    failure ends   (only if a notice was sent, send_recovery_notice)------>  RECOVERY
    node Unknown   --warn_when_unknown_after_minutes (once)--------------->  UNKNOWN_WARNING
    Unknown clears (after a warning)--------------------------------------> UNKNOWN_CLEARED

A component is failing when a node is confirmed Down and ``notify_on_partial_outage`` is set, or the
whole component is Down (and, with ``notify_on_degraded``, when a node is Degraded). During a
maintenance window covering the component nothing is sent and the timer restarts at the window end.
While a hard dependency is fully Down, other failing components send nothing themselves; they are
listed in the root-cause notice (``group_downstream_under_root_cause``).
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum

from hfm_dashboard.domain.rules import CloseCalendar, Rules
from hfm_dashboard.domain.snapshot import NodeKey
from hfm_dashboard.domain.status import NodeState, Status


class NoticeKind(StrEnum):
    FIRST = "first"
    REMINDER = "reminder"
    ESCALATION = "escalation"
    RECOVERY = "recovery"
    UNKNOWN_WARNING = "unknown_warning"
    UNKNOWN_CLEARED = "unknown_cleared"


@dataclass(frozen=True, slots=True)
class MaintenanceWindow:
    """``component`` None covers every component of the environment. Times UTC, end exclusive."""

    start: datetime
    end: datetime
    component: str | None = None
    reason: str = ""

    def covers(self, component: str, at: datetime) -> bool:
        return (self.component is None or self.component == component) and self.start <= at < self.end


@dataclass(frozen=True, slots=True)
class ComponentNoticeState:
    failing_since: datetime | None = None    # confirmed start of the current failure
    clock_start: datetime | None = None      # timer start (moved to the end of a maintenance window)
    first_sent_at: datetime | None = None
    last_sent_at: datetime | None = None
    reminders_sent: int = 0
    escalated: bool = False
    grouped_under: str | None = None         # root-cause component while grouped
    unknown_since: datetime | None = None
    unknown_warned: bool = False


@dataclass(frozen=True, slots=True)
class Notice:
    kind: NoticeKind
    environment: str
    component: str
    display_name: str
    at: datetime
    since: datetime | None                   # failure (or Unknown) start
    affected_nodes: tuple[str, ...]
    total_nodes: int
    full_outage: bool                        # every node of the component is Down
    escalate: bool                           # include the manager
    close_period: bool
    downstream: tuple[str, ...] = field(default=())     # components grouped under this root cause
    details: tuple[str, ...] = field(default=())        # node reasons, "node: detail"


# --------------------------------------------------------------------------- close calendar

def _business_days(year: int, month: int) -> list[date]:
    days = calendar.monthrange(year, month)[1]
    return [d for d in (date(year, month, n) for n in range(1, days + 1)) if d.weekday() < 5]


def is_close_period(day: date, cal: CloseCalendar) -> bool:
    """True on the first N and last M business days (Mon-Fri) of a month, and on ``extra_dates``.

    Days are UTC calendar days (hard rule 5). ``quarter_end_months`` only labels the period
    (``close_label``); the windows apply to every month (ASSUMED Q46/Q61).
    """
    if day in cal.extra_dates:
        return True
    bdays = _business_days(day.year, day.month)
    head = bdays[:cal.business_days_after_month_start]
    tail = bdays[-cal.business_days_before_month_end:] if cal.business_days_before_month_end else []
    return day in head or day in tail


def close_label(day: date, cal: CloseCalendar) -> str:
    """'quarter-end close' / 'month-end close' / '' for notice texts and the dashboard.

    The last business days of a month close that month; the first business days of a month belong
    to the close of the previous month.
    """
    if not is_close_period(day, cal):
        return ""
    bdays = _business_days(day.year, day.month)
    head = bdays[:cal.business_days_after_month_start]
    closing_month = (day.month - 2) % 12 + 1 if day in head else day.month
    return "quarter-end close" if closing_month in cal.quarter_end_months else "month-end close"


# --------------------------------------------------------------------------- engine

def _failing_nodes(nodes: Mapping[str, NodeState], comp_status: Status, rules: Rules) -> list[str]:
    """Nodes that make the component 'failing' for notices (empty list: not failing)."""
    n = rules.notices
    notify_states = {Status.DOWN} | ({Status.DEGRADED} if n.notify_on_degraded else set())
    failing = [node for node, st in nodes.items() if st.status in notify_states]
    if comp_status is Status.DOWN:                       # whole component down: always notify
        return failing or list(nodes)
    if not failing:
        return []
    if n.notify_on_degraded:                             # Degraded (or Down) nodes notify
        return failing
    if n.notify_on_partial_outage:                       # some, not all, nodes Down
        return failing
    return []


def evaluate_notices(
    environment: str,
    previous: Mapping[str, ComponentNoticeState],
    node_states: Mapping[NodeKey, NodeState],
    component_status: Mapping[str, Status],
    rules: Rules,
    now: datetime,
    maintenance: Sequence[MaintenanceWindow] = (),
) -> tuple[dict[str, ComponentNoticeState], list[Notice]]:
    n = rules.notices
    close = is_close_period(now.date(), rules.close_calendar)
    delay = timedelta(minutes=n.first_notice_after_minutes_close if close else n.first_notice_after_minutes)
    repeat = timedelta(minutes=n.repeat_every_minutes)
    escalate_after = timedelta(minutes=n.escalate_to_manager_after_minutes)
    warn_after = timedelta(minutes=n.warn_when_unknown_after_minutes)
    suppress = rules.maintenance.suppress_notices_in_window

    by_comp: dict[str, dict[str, NodeState]] = {}
    for (cid, node), st in node_states.items():
        by_comp.setdefault(cid, {})[node] = st

    failing: dict[str, list[str]] = {
        cid: _failing_nodes(nodes, component_status.get(cid, Status.UNKNOWN), rules)
        for cid, nodes in by_comp.items()
    }
    roots = sorted(cid for cid in by_comp if rules.components[cid].hard_dependency
                   and component_status.get(cid) is Status.DOWN)
    root = roots[0] if roots and n.group_downstream_under_root_cause else None
    downstream = tuple(sorted(c for c, f in failing.items() if f and c not in roots)) if root else ()

    states: dict[str, ComponentNoticeState] = {}
    notices: list[Notice] = []
    for cid, nodes in sorted(by_comp.items()):
        prev = previous.get(cid, ComponentNoticeState())
        comp = rules.components[cid]
        windows = [w for w in maintenance if w.covers(cid, now)] if suppress else []
        in_window = bool(windows)
        bad = failing[cid]

        def notice(kind: NoticeKind, since: datetime | None, affected: list[str], escalate: bool = False,
                   extra: tuple[str, ...] = ()) -> Notice:
            return Notice(kind, environment, cid, comp.display_name, now, since, tuple(affected), len(nodes),
                          component_status.get(cid) is Status.DOWN, escalate, close, extra,
                          tuple(f"{x}: {nodes[x].detail}" for x in affected if nodes[x].detail))

        # ---- outage notices
        st = prev
        if bad:
            # A new failure starts when the component became failing: the latest confirmed Down among
            # its failing nodes. An ongoing failure keeps its start.
            failing_since = prev.failing_since or max((nodes[x].since or now) for x in bad)
            clock = max(w.end for w in windows) if in_window else (prev.clock_start or failing_since)
            grouped = root if (root and cid != root and cid not in roots) else None
            st = replace(prev, failing_since=failing_since, clock_start=clock, grouped_under=grouped)
            if not in_window and not grouped:
                extra = downstream if cid == root else ()
                if st.first_sent_at is None:
                    if now - clock >= delay:
                        notices.append(notice(NoticeKind.FIRST, failing_since, bad, now - clock >= escalate_after, extra))
                        st = replace(st, first_sent_at=now, last_sent_at=now,
                                     escalated=now - clock >= escalate_after)
                elif not st.escalated and now - clock >= escalate_after:
                    notices.append(notice(NoticeKind.ESCALATION, failing_since, bad, True, extra))
                    st = replace(st, escalated=True, last_sent_at=now)
                elif st.last_sent_at is not None and now - st.last_sent_at >= repeat:
                    notices.append(notice(NoticeKind.REMINDER, failing_since, bad, st.escalated, extra))
                    st = replace(st, last_sent_at=now, reminders_sent=st.reminders_sent + 1)
        elif prev.failing_since is not None:
            if prev.first_sent_at is not None and n.send_recovery_notice and not in_window:
                notices.append(notice(NoticeKind.RECOVERY, prev.failing_since, [], prev.escalated))
            st = replace(prev, failing_since=None, clock_start=None, first_sent_at=None, last_sent_at=None,
                         reminders_sent=0, escalated=False, grouped_under=None)

        # ---- monitoring (Unknown) warnings
        unknown = [x for x, s in nodes.items() if s.status is Status.UNKNOWN]
        if unknown:
            since = min((nodes[x].since or now) for x in unknown)
            u_since = min(st.unknown_since, since) if st.unknown_since else since
            st = replace(st, unknown_since=u_since)
            if not st.unknown_warned and not in_window and now - u_since >= warn_after:
                notices.append(notice(NoticeKind.UNKNOWN_WARNING, u_since, unknown))
                st = replace(st, unknown_warned=True)
        elif st.unknown_since is not None:
            if st.unknown_warned and not in_window:
                notices.append(notice(NoticeKind.UNKNOWN_CLEARED, st.unknown_since, []))
            st = replace(st, unknown_since=None, unknown_warned=False)

        states[cid] = st
    return states, notices
