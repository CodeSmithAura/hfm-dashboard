"""Fold one poll's check results into confirmed node states. Pure: no I/O, no clock.

    check results --worst per node-->  raw node status
    raw status    --flap protection--> confirmed NodeState (status.advance)

Every check a component declares in rules.yaml is expected on each of its nodes in the
environment. An expected check with no result this poll (its collector crashed or was not wired)
counts as Unknown, so a broken collector can never make a node look healthy (D13, D24).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime

from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.status import CheckResult, NodeState, Status, advance, worst

NodeKey = tuple[str, str]   # (component, node)


def expected_checks(env: Environment, rules: Rules) -> dict[NodeKey, tuple[str, ...]]:
    """Every (component, node) monitored in this environment and the checks expected on it."""
    return {
        (cid, node): tuple(comp.checks)
        for cid, comp in rules.enabled_components().items()
        for node in env.nodes_of(cid, rules)
    }


def component_nodes(env: Environment, rules: Rules) -> dict[str, list[str]]:
    """Scoring scope: the nodes each enabled component has in this environment."""
    return {cid: env.nodes_of(cid, rules) for cid in rules.enabled_components()}


def fold_poll(
    previous: Mapping[NodeKey, NodeState],
    results: Iterable[CheckResult],
    expected: Mapping[NodeKey, tuple[str, ...]],
    rules: Rules,
    observed_at: datetime,
) -> dict[NodeKey, NodeState]:
    by_node: dict[NodeKey, dict[str, CheckResult]] = defaultdict(dict)
    for r in results:
        key = (r.component, r.node)
        if key not in expected:
            raise ValueError(f"result for unexpected node {key} ({r.check})")
        by_node[key][r.check] = r

    confirm = rules.checks.confirm_down_after_consecutive_failures
    states: dict[NodeKey, NodeState] = {}
    for key, checks in expected.items():
        got = by_node.get(key, {})
        statuses: list[Status] = []
        problems: list[str] = []
        for check in checks:
            r = got.get(check)
            if r is None:
                statuses.append(Status.UNKNOWN)
                problems.append(f"{check}: no result this poll")
            else:
                statuses.append(r.status)
                if r.status is not Status.UP:
                    problems.append(r.detail or f"{check}: {r.status.value}")
        prev = previous.get(key) or NodeState(component=key[0], node=key[1])
        states[key] = advance(prev, worst(statuses), observed_at, confirm, detail="; ".join(problems))
    return states


def node_statuses(states: Mapping[NodeKey, NodeState]) -> dict[str, dict[str, Status]]:
    """Confirmed statuses in the shape scoring expects: component -> node -> status."""
    out: dict[str, dict[str, Status]] = defaultdict(dict)
    for (cid, node), state in states.items():
        out[cid][node] = state.status
    return dict(out)
