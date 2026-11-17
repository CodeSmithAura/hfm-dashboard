"""Disk space and event log collector: one WinRM query per host, in parallel.

``disk`` applies to every component on the node that lists it (a nearly full volume affects every
service there). ``event_log`` uses each component's own Windows service name for crash events;
an unexpected server shutdown/restart applies to every component on the node. A host that cannot be
queried gives Unknown for both checks of every component on it.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.evaluation import CollectionFailure, DiskObservation, EventLogObservation, evaluate
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.status import CheckResult
from hfm_dashboard.ports.host_health import HostHealth, HostHealthSource, HostQueryError

CHECKS = ("disk", "event_log")


def plan(env: Environment, rules: Rules) -> dict[str, list[tuple[str, str, str | None]]]:
    """host -> [(component, node, service name or None)] for components with disk/event_log checks."""
    by_host: dict[str, list[tuple[str, str, str | None]]] = defaultdict(list)
    for cid, comp in rules.enabled_components().items():
        if not any(c in comp.checks for c in CHECKS):
            continue
        for node in env.nodes_of(cid, rules):
            service = env.service_name(cid, node) if "event_log" in comp.checks else None
            by_host[env.nodes[node].host].append((cid, node, service))
    return dict(by_host)


def collect_host_health(env: Environment, rules: Rules, source: HostHealthSource, observed_at: datetime,
                        max_workers: int = 8) -> list[CheckResult]:
    targets = plan(env, rules)
    if not targets:
        return []
    rule = rules.checks.event_log

    def query(host: str) -> HostHealth | HostQueryError:
        names = list(dict.fromkeys(s for _, _, s in targets[host] if s))
        try:
            return source.query(host, names, rule.lookback_minutes, rule.service_crash_event_ids,
                                rule.unexpected_shutdown_event_ids)
        except HostQueryError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as pool:
        answers = dict(zip(targets, pool.map(query, targets)))

    results = []
    for host, entries in targets.items():
        answer = answers[host]
        for cid, node, service in entries:
            comp = rules.components[cid]
            for check in CHECKS:
                if check not in comp.checks:
                    continue
                if isinstance(answer, HostQueryError):
                    obs = CollectionFailure(str(answer))
                elif check == "disk":
                    obs = DiskObservation(answer.volumes)
                else:
                    obs = EventLogObservation(answer.service_crashes.get(service or "", 0),
                                              answer.unexpected_shutdowns)
                ev = evaluate(check, obs, rules.checks)
                detail = (f"event log ({service}): {ev.detail}" if check == "event_log" and service
                          else f"disk: {ev.detail}" if check == "disk" else f"event log: {ev.detail}")
                results.append(CheckResult(component=cid, node=node, check=check, status=ev.status,
                                           observed_at=observed_at, detail=detail))
    return results
