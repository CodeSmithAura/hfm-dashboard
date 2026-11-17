"""Compose notice emails (pure: no I/O). Subject, plain-text body and recipients.

Recipients: component owners for outage and recovery notices, managers copied once escalated;
the monitoring team for "cannot monitor" warnings. Times are shown in UTC (hard rule 5). Host names
appear only in the email (built at runtime from environments.yaml), never in the repository.
Restart guidance lists the start order from rules.yaml; the monitor itself never restarts anything
(hard rule 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.notices import Notice, NoticeKind
from hfm_dashboard.domain.owners import OwnersConfig
from hfm_dashboard.domain.rules import Rules


@dataclass(frozen=True, slots=True)
class OutgoingMail:
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body: str


_OUTAGE = (NoticeKind.FIRST, NoticeKind.REMINDER, NoticeKind.ESCALATION)


def _utc(at: datetime | None) -> str:
    return at.strftime("%Y-%m-%d %H:%M UTC") if at else "-"


def _duration(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def _label(n: Notice) -> str:
    outage = "DOWN" if n.full_outage else "PARTIAL OUTAGE"
    return {
        NoticeKind.FIRST: outage,
        NoticeKind.REMINDER: f"STILL {outage}",
        NoticeKind.ESCALATION: f"ESCALATED - {outage}",
        NoticeKind.RECOVERY: "RECOVERED",
        NoticeKind.UNKNOWN_WARNING: "CANNOT MONITOR",
        NoticeKind.UNKNOWN_CLEARED: "MONITORING RESTORED",
    }[n.kind]


def _nodes_phrase(n: Notice) -> str:
    if n.full_outage:
        return f"all {n.total_nodes} node(s)"
    return f"{len(n.affected_nodes)} of {n.total_nodes} node(s)"


def recipients(n: Notice, owners: OwnersConfig) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """(to, cc), or None when nobody is configured for the component."""
    if n.kind in (NoticeKind.UNKNOWN_WARNING, NoticeKind.UNKNOWN_CLEARED):
        return tuple(owners.monitoring_team), ()
    contacts = owners.contacts_for(n.component)
    if contacts is None:
        return None
    return tuple(contacts.owners), tuple(contacts.managers) if n.escalate else ()


def compose(n: Notice, owners: OwnersConfig, rules: Rules, env: Environment) -> OutgoingMail | None:
    who = recipients(n, owners)
    if who is None:
        return None
    to, cc = who

    subject = f"[HFM {n.environment}] {_label(n)}: {n.display_name}"
    if n.kind in _OUTAGE:
        subject += f" - {_nodes_phrase(n)} since {n.since.strftime('%H:%M UTC') if n.since else '-'}"
    elif n.kind is NoticeKind.UNKNOWN_WARNING:
        subject += f" - {len(n.affected_nodes)} node(s) cannot be checked"

    reasons = dict(d.split(": ", 1) for d in n.details if ": " in d)
    lines = [f"HFM availability notice - {env.display_name}", "",
             f"Component: {n.display_name} ({n.component})"]

    if n.kind in _OUTAGE:
        state = "Full outage" if n.full_outage else "Partial outage"
        lines += [f"Status:    {state}: {_nodes_phrase(n)} Down",
                  f"Since:     {_utc(n.since)} ({_duration(n.at - n.since) if n.since else '-'} ago)"]
        if n.kind is NoticeKind.ESCALATION:
            lines.append(f"Escalated: still not restored after "
                         f"{rules.notices.escalate_to_manager_after_minutes:g} minutes; managers copied")
        elif n.escalate:
            lines.append("Escalated: managers copied")
    elif n.kind is NoticeKind.RECOVERY:
        lines += ["Status:    Recovered",
                  f"Outage:    {_utc(n.since)} to {_utc(n.at)} ({_duration(n.at - n.since) if n.since else '-'})"]
    elif n.kind is NoticeKind.UNKNOWN_WARNING:
        lines += ["Status:    Cannot be monitored - the checks for these nodes could not run",
                  f"Since:     {_utc(n.since)} ({_duration(n.at - n.since) if n.since else '-'} ago)",
                  "Meaning:   the application may be fine; the monitoring server cannot reach or query",
                  "           the nodes (network, WinRM, credentials). It is left out of the score."]
    else:
        lines += ["Status:    Monitoring restored - the checks run again",
                  f"Gap:       {_utc(n.since)} to {_utc(n.at)}"]

    if n.close_period and n.kind in _OUTAGE:
        lines.append(f"Period:    close period (first notice after "
                     f"{rules.notices.first_notice_after_minutes_close:g} minutes)")

    if n.affected_nodes:
        lines += ["", "Affected nodes:"]
        for node in n.affected_nodes:
            host = env.nodes[node].host if node in env.nodes else ""
            reason = reasons.get(node, "")
            lines.append(f"  - {node}{f' ({host})' if host else ''}{f': {reason}' if reason else ''}")

    if n.downstream:
        names = [rules.components[c].display_name for c in n.downstream if c in rules.components]
        lines += ["", "Also affected (grouped under this root cause, no separate notices):",
                  *[f"  - {name}" for name in names]]

    if rules.notices.include_restart_guidance and n.kind in _OUTAGE:
        lines += ["", *restart_guidance(n, rules, env)]

    if env.notices.dashboard_url:
        lines += ["", f"Dashboard: {env.notices.dashboard_url}"]
    lines += ["", "Sent by the HFM availability monitor. Times are UTC. Please do not reply to this message."]
    return OutgoingMail(to, cc, subject, "\n".join(lines) + "\n")


def restart_guidance(n: Notice, rules: Rules, env: Environment) -> list[str]:
    order = rules.notices.restart_order
    lines = ["Restart guidance (the monitor never restarts anything):"]
    if order:
        steps = ", ".join(f"{i}. {rules.components[c].display_name}" for i, c in enumerate(order, 1))
        lines.append(f"  Start order: {steps}")
        if n.component in order:
            step = order.index(n.component) + 1
            lines.append(f"  This component is step {step}; make sure steps 1-{step - 1} are running first."
                         if step > 1 else "  This component is step 1.")
    services = env.services.get(n.component, {})
    named = [(node, services[node]) for node in n.affected_nodes if node in services]
    if named:
        lines.append("  Windows service on each affected node:")
        lines += [f"    - {node}: {svc}" for node, svc in named]
    lines.append("  Each EPM instance is started and stopped with its own bin\\start.bat / stop.bat.")
    return lines
