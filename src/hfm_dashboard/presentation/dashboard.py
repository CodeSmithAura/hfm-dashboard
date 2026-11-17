"""Dashboard view model: one environment's latest poll, notices and maintenance, ready to display.

Pure: no I/O, no clock (``now`` is passed in), no web framework. ``build_dashboard`` returns plain
dataclasses; ``as_dict`` turns them into the JSON the API serves (contract: docs/ui.md, version 1).
Templates only print these fields - they never compute statuses, order, durations or wording.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from hfm_dashboard.app.poller import PollSnapshot
from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.notices import MaintenanceWindow, Notice, NoticeKind
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.scoring import Band
from hfm_dashboard.domain.status import CheckResult, Status

CONTRACT_VERSION = 1

STATUS_LABEL = {"up": "Up", "degraded": "Degraded", "down": "Down", "unknown": "Unknown",
                "maintenance": "Maintenance", "none": "No data"}
BAND_LABEL = {Band.GREEN: "Green", Band.AMBER: "Amber", Band.RED: "Red", Band.UNKNOWN: "Unknown"}
NOTICE_LABEL = {
    NoticeKind.FIRST: "First notice", NoticeKind.REMINDER: "Reminder", NoticeKind.ESCALATION: "Escalated",
    NoticeKind.RECOVERY: "Recovered", NoticeKind.UNKNOWN_WARNING: "Cannot monitor",
    NoticeKind.UNKNOWN_CLEARED: "Monitoring restored",
}
CHECK_LABEL = {"windows_service": "Windows service", "weblogic_server": "WebLogic server",
               "sql_server": "SQL Server health", "disk": "Disk space", "event_log": "Event log"}
_ATTENTION_ORDER = {"down": 0, "degraded": 1, "unknown": 2, "maintenance": 3}
STALE_AFTER_POLLS = 3


# --------------------------------------------------------------------------- view model

@dataclass(frozen=True)
class EnvTab:
    id: str
    name: str
    active: bool


@dataclass(frozen=True)
class Banner:
    kind: str          # cap | maintenance | notices_off | stale | no_data
    text: str


@dataclass(frozen=True)
class ScoreView:
    pct: float | None
    pct_text: str
    band: str          # green | amber | red | unknown
    band_label: str
    capped_by: tuple[str, ...]


@dataclass(frozen=True)
class Counts:
    total: int
    up: int
    degraded: int
    down: int
    unknown: int


@dataclass(frozen=True)
class NodeHead:
    id: str
    label: str


@dataclass(frozen=True)
class Cell:
    node: str
    applicable: bool
    status: str = "none"      # up | degraded | down | unknown | maintenance | none
    label: str = ""
    select: str = ""          # value for ?sel= to show this cell's detail
    selected: bool = False
    aria: str = ""


@dataclass(frozen=True)
class Row:
    id: str
    name: str
    meta: str
    hard_dependency: bool
    status: str
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class CheckLine:
    check: str
    label: str
    status: str
    status_label: str
    detail: str


@dataclass(frozen=True)
class Detail:
    component: str
    name: str
    node: str
    node_label: str
    host: str
    status: str
    status_label: str
    since: str                # ISO UTC or ""
    since_text: str
    checks: tuple[CheckLine, ...]


@dataclass(frozen=True)
class AttentionItem:
    component: str
    name: str
    node_label: str
    status: str
    status_label: str
    detail: str
    since: str
    duration_text: str


@dataclass(frozen=True)
class NoticeLine:
    at: str
    kind: str
    kind_label: str
    tone: str                 # status-like tone for styling
    component: str
    name: str
    text: str
    delivery: str


@dataclass(frozen=True)
class ProbeLine:
    check: str
    name: str
    path: str
    status: str
    status_label: str
    detail: str


@dataclass(frozen=True)
class ComponentScore:
    id: str
    name: str
    weight: int
    pct: float | None
    pct_text: str
    status: str


@dataclass(frozen=True)
class DashboardView:
    contract_version: int
    environment: str
    environment_name: str
    notices_enabled: bool
    environments: tuple[EnvTab, ...]
    observed_at: str              # ISO UTC of the poll shown, "" when none yet
    generated_at: str
    poll_interval_seconds: int
    score: ScoreView
    counts: Counts
    banners: tuple[Banner, ...]
    nodes: tuple[NodeHead, ...]
    rows: tuple[Row, ...]
    selected: Detail | None
    attention: tuple[AttentionItem, ...]
    notices: tuple[NoticeLine, ...]
    load_balancer: tuple[ProbeLine, ...]
    component_scores: tuple[ComponentScore, ...]
    lb_note: str = field(default="Shown, not scored until Q57 is answered")


def as_dict(view: DashboardView) -> dict:
    return asdict(view)


# --------------------------------------------------------------------------- helpers

def node_label(node: str) -> str:
    """``web_node_01`` -> ``Web 1``; anything else unchanged."""
    m = re.fullmatch(r"([a-z]+)_node_0*(\d+)", node)
    if not m:
        return node
    prefix = m.group(1)
    return f"{prefix.upper() if len(prefix) <= 3 and prefix != 'web' else prefix.capitalize()} {m.group(2)}"


def duration_text(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days} d {hours} h"
    return f"{hours} h {mins:02d} min" if hours else f"{mins} min"


def _iso(at: datetime | None) -> str:
    return at.astimezone(UTC).isoformat() if at else ""


def _utc(at: datetime | None) -> str:
    return at.astimezone(UTC).strftime("%d %b %H:%M UTC") if at else ""


def _in_window(windows: Sequence[MaintenanceWindow], component: str, at: datetime) -> MaintenanceWindow | None:
    return next((w for w in windows if w.covers(component, at)), None)


# --------------------------------------------------------------------------- builder

def build_dashboard(
    environment: str,
    env: Environment,
    rules: Rules,
    snapshot: PollSnapshot | None,
    now: datetime,
    environments: Sequence[tuple[str, str]],
    notices_today: Sequence[tuple[Notice, bool, str]] = (),
    maintenance: Sequence[MaintenanceWindow] = (),
    selected: str = "",
) -> DashboardView:
    enabled = rules.enabled_components()
    nodes = [n for n in env.nodes if any(n in c.nodes for c in enabled.values())]
    env_rules = rules.environments.get(environment)
    notices_enabled = bool(env_rules and env_rules.notices_enabled)

    tabs = tuple(EnvTab(eid, name, eid == environment) for eid, name in environments)
    banners: list[Banner] = []
    if not notices_enabled:
        banners.append(Banner("notices_off", f"{env.display_name}: dashboard only. Notices are worked out and "
                                             "logged but not emailed."))

    if snapshot is None:
        banners.insert(0, Banner("no_data", "Waiting for the first poll."))
        empty_rows = tuple(
            Row(cid, c.display_name, _meta(c, rules), c.hard_dependency, "none",
                tuple(Cell(n, n in env.nodes_of(cid, rules)) for n in nodes))
            for cid, c in enabled.items())
        return DashboardView(
            CONTRACT_VERSION, environment, env.display_name, notices_enabled, tabs, "", _iso(now),
            rules.collection.poll_interval_seconds, ScoreView(None, "n/a", "unknown", "Unknown", ()),
            Counts(len(enabled), 0, 0, 0, len(enabled)), tuple(banners),
            tuple(NodeHead(n, node_label(n)) for n in nodes), empty_rows, None, (), (), (), ())

    at = snapshot.observed_at
    score = snapshot.score
    if now - at > timedelta(seconds=STALE_AFTER_POLLS * rules.collection.poll_interval_seconds):
        banners.insert(0, Banner("stale", f"Data is stale: last poll {_utc(at)} ({duration_text(now - at)} ago)."))
    if score.capped_by:
        names = ", ".join(enabled[c].display_name for c in score.capped_by)
        banners.insert(0, Banner("cap", f"Score capped at {rules.scoring.hard_dependency_cap_pct:g}%: {names} "
                                        "(hard dependency) is fully down. Other notices are grouped under it."))
    active = [w for w in maintenance if w.start <= now < w.end]
    for w in active:
        what = enabled[w.component].display_name if w.component in enabled else "all components"
        reason = f" ({w.reason})" if w.reason else ""
        banners.append(Banner("maintenance", f"Maintenance until {_utc(w.end)}: {what}{reason}. "
                                             "Notices are suppressed; the timer restarts when the window ends."))

    results: dict[tuple[str, str], list[CheckResult]] = {}
    for r in snapshot.results:
        results.setdefault((r.component, r.node), []).append(r)

    rows: list[Row] = []
    attention: list[AttentionItem] = []
    detail: Detail | None = None
    counts = {"up": 0, "degraded": 0, "down": 0, "unknown": 0}
    for cid, comp in enabled.items():
        cs = score.components.get(cid)
        comp_status = cs.status.value if cs else "unknown"
        counts[comp_status] = counts.get(comp_status, 0) + 1
        cells = []
        for n in nodes:
            if n not in env.nodes_of(cid, rules):
                cells.append(Cell(n, False))
                continue
            st = snapshot.node_states.get((cid, n))
            status = st.status.value if st else "unknown"
            window = _in_window(active, cid, now)
            shown = "maintenance" if window and status != "up" else status
            key = f"{cid}.{n}"
            is_sel = key == selected
            cells.append(Cell(n, True, shown, STATUS_LABEL[shown], key, is_sel,
                              f"{comp.display_name} on {node_label(n)}: {STATUS_LABEL[shown]}"))
            if st and status != "up":
                attention.append(AttentionItem(
                    cid, comp.display_name, node_label(n), shown, STATUS_LABEL[shown], st.detail or "",
                    _iso(st.since), duration_text(now - st.since) if st.since else ""))
            if is_sel:
                detail = Detail(
                    cid, comp.display_name, n, node_label(n), env.nodes[n].host, shown, STATUS_LABEL[shown],
                    _iso(st.since if st else None),
                    f"{STATUS_LABEL[status]} since {_utc(st.since)}" if st and st.since else "",
                    tuple(_check_line(r) for r in sorted(results.get((cid, n), []), key=lambda r: comp.checks.index(r.check)
                                                         if r.check in comp.checks else 99)))
        rows.append(Row(cid, comp.display_name, _meta(comp, rules), comp.hard_dependency, comp_status, tuple(cells)))

    attention.sort(key=lambda a: (_ATTENTION_ORDER.get(a.status, 9), a.since or "~"))

    notices = tuple(
        NoticeLine(_iso(n.at), n.kind.value, NOTICE_LABEL[n.kind], _notice_tone(n), n.component, n.display_name,
                   _notice_text(n), "emailed" if delivered else (note or "not emailed"))
        for n, delivered, note in sorted(notices_today, key=lambda x: x[0].at, reverse=True))

    probe_names = {ref.partition(":")[2]: c.display_name for c in enabled.values()
                   for ref in c.checks if ref.startswith("url_probe:")}
    probes = tuple(
        ProbeLine(r.check, probe_names.get(r.check.partition(":")[2], r.check.partition(":")[2]),
                  env.url_probes.probes[r.check.partition(":")[2]].path
                  if r.check.partition(":")[2] in env.url_probes.probes else "",
                  r.status.value, STATUS_LABEL[r.status.value], r.detail.split(": ", 1)[-1])
        for r in snapshot.load_balancer)

    scores = tuple(
        ComponentScore(cid, enabled[cid].display_name, c.weight, c.score_pct,
                       "n/a" if c.score_pct is None else f"{round(c.score_pct, 2):g}%", c.status.value)
        for cid, c in score.components.items())

    return DashboardView(
        CONTRACT_VERSION, environment, env.display_name, notices_enabled, tabs, _iso(at), _iso(now),
        rules.collection.poll_interval_seconds,
        ScoreView(score.overall_pct, "n/a" if score.overall_pct is None else f"{score.overall_pct:.2f}%",
                  score.band.value, BAND_LABEL[score.band] + (" · capped" if score.capped_by else ""),
                  tuple(enabled[c].display_name for c in score.capped_by)),
        Counts(len(enabled), counts["up"], counts["degraded"], counts["down"], counts["unknown"]),
        tuple(banners), tuple(NodeHead(n, node_label(n)) for n in nodes), tuple(rows), detail,
        tuple(attention), notices, probes, scores)


def _meta(comp, rules: Rules) -> str:
    weight = rules.criticality_weights[comp.criticality]
    return f"{comp.criticality} · weight {weight}" + (" · hard dependency" if comp.hard_dependency else "")


def _check_line(r: CheckResult) -> CheckLine:
    kind, _, arg = r.check.partition(":")
    label = f"URL probe {arg.replace('_', ' ')}" if kind == "url_probe" else CHECK_LABEL.get(kind, kind)
    return CheckLine(r.check, label, r.status.value, STATUS_LABEL[r.status.value], r.detail)


def _notice_tone(n: Notice) -> str:
    if n.kind in (NoticeKind.RECOVERY, NoticeKind.UNKNOWN_CLEARED):
        return "up"
    if n.kind is NoticeKind.UNKNOWN_WARNING:
        return "unknown"
    return "down" if n.full_outage else "degraded"


def _notice_text(n: Notice) -> str:
    since = f" since {n.since.strftime('%H:%M UTC')}" if n.since else ""
    if n.kind in (NoticeKind.RECOVERY, NoticeKind.UNKNOWN_CLEARED):
        return f"{n.display_name} - restored"
    if n.kind is NoticeKind.UNKNOWN_WARNING:
        return f"{n.display_name} - {len(n.affected_nodes)} node(s) cannot be checked{since}"
    scope = f"all {n.total_nodes} node(s)" if n.full_outage else f"{len(n.affected_nodes)} of {n.total_nodes} node(s)"
    extra = f"; also affected: {', '.join(n.downstream)}" if n.downstream else ""
    return f"{n.display_name} - {scope} down{since}{extra}"
