"""Stability scoring: a pure function of (confirmed node statuses, rules).

    node status  --node_points-->  node points
    node points  --node share-->   component score (mean over the component's nodes)
    components   --criticality weights-->  overall score (weighted average)
    hard dependency Down  -->  overall capped at hard_dependency_cap_pct
    overall      --bands-->        Green / Amber / Red

No I/O, no clock, no globals: every number comes from ``Rules``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .rules import Rules
from .status import Status

# Overall score is rounded to this many decimals before banding, so float noise such as
# 94.99999999 does not flip a band. This is presentation precision, not a behaviour rule.
_DECIMALS = 2


class Band(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    UNKNOWN = "unknown"  # no component could be checked


@dataclass(frozen=True, slots=True)
class ComponentScore:
    component: str
    display_name: str
    status: Status
    score_pct: float | None          # None when no node of the component could be checked
    weight: int
    hard_dependency: bool
    nodes: Mapping[str, Status]


@dataclass(frozen=True, slots=True)
class StabilityScore:
    overall_pct: float | None        # after the hard-dependency cap; None when nothing is known
    uncapped_pct: float | None       # weighted average before the cap
    band: Band
    capped_by: tuple[str, ...]       # hard-dependency components that are Down
    components: Mapping[str, ComponentScore]


NodeStatuses = Mapping[str, Mapping[str, Status]]   # component -> node -> confirmed status


def score(node_statuses: NodeStatuses, rules: Rules) -> StabilityScore:
    """Score the application. Nodes missing from ``node_statuses`` count as Unknown."""
    enabled = rules.enabled_components()
    _reject_unexpected(node_statuses, rules)

    components = {
        cid: _score_component(cid, node_statuses.get(cid, {}), rules) for cid in enabled
    }

    known = [c for c in components.values() if c.score_pct is not None]
    if known:
        total_weight = sum(c.weight for c in known)
        uncapped = sum(c.score_pct * c.weight for c in known) / total_weight  # type: ignore[operator]
        uncapped = round(uncapped, _DECIMALS)
    else:
        uncapped = None

    capped_by = tuple(
        cid for cid, c in components.items() if c.hard_dependency and c.status is Status.DOWN
    )
    overall = uncapped
    if capped_by and overall is not None:
        overall = min(overall, rules.scoring.hard_dependency_cap_pct)

    return StabilityScore(
        overall_pct=overall,
        uncapped_pct=uncapped,
        band=band_for(overall, rules),
        capped_by=capped_by,
        components=components,
    )


def band_for(pct: float | None, rules: Rules) -> Band:
    if pct is None:
        return Band.UNKNOWN
    bands = rules.scoring.bands
    if pct >= bands.green_min_pct:
        return Band.GREEN
    if pct >= bands.amber_min_pct:
        return Band.AMBER
    return Band.RED


def _score_component(cid: str, reported: Mapping[str, Status], rules: Rules) -> ComponentScore:
    comp = rules.components[cid]
    nodes = {node: reported.get(node, Status.UNKNOWN) for node in comp.nodes}

    if rules.scoring.unknown_nodes == "count_as_down":
        effective = [Status.DOWN if s is Status.UNKNOWN else s for s in nodes.values()]
    else:  # exclude
        effective = [s for s in nodes.values() if s is not Status.UNKNOWN]

    if not effective:
        status, pct = Status.UNKNOWN, None
    else:
        points = rules.scoring.node_points
        per_status = {Status.UP: points.up, Status.DEGRADED: points.degraded, Status.DOWN: points.down}
        pct = sum(per_status[s] for s in effective) / len(effective)
        if all(s is Status.UP for s in effective):
            status = Status.UP
        elif all(s is Status.DOWN for s in effective):
            status = Status.DOWN
        else:
            status = Status.DEGRADED   # CONFIRMED Q27: partly failed multi-node component is Degraded

    return ComponentScore(
        component=cid,
        display_name=comp.display_name,
        status=status,
        score_pct=pct,
        weight=rules.weight_of(cid),
        hard_dependency=comp.hard_dependency,
        nodes=nodes,
    )


def _reject_unexpected(node_statuses: NodeStatuses, rules: Rules) -> None:
    """A status for a component or node rules.yaml does not monitor is a wiring bug: fail loudly."""
    enabled = rules.enabled_components()
    for cid, nodes in node_statuses.items():
        if cid not in enabled:
            raise ValueError(f"status reported for unmonitored component {cid!r}")
        extra = set(nodes) - set(enabled[cid].nodes)
        if extra:
            raise ValueError(f"component {cid!r}: status reported for unknown nodes {sorted(extra)}")
