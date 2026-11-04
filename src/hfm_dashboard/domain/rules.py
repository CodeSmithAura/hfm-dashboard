"""Typed, validated model of config/rules.yaml.

Every section rejects unknown keys, so a typo in rules.yaml stops the program at start-up
instead of silently falling back to a default. Defaults below exist only for optional flags;
every threshold, weight and timing must be present in the file.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Pct = Annotated[float, Field(ge=0, le=100)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- 1. checks

class StateMapCheck(_Strict):
    """Maps a state string reported by the target to Up / Degraded / Down."""

    up: list[str]
    degraded: list[str]
    down: list[str]

    @model_validator(mode="after")
    def _disjoint(self) -> StateMapCheck:
        seen: dict[str, str] = {}
        for bucket in ("up", "degraded", "down"):
            for state in getattr(self, bucket):
                if state in seen:
                    raise ValueError(f"state {state!r} listed under both {seen[state]} and {bucket}")
                seen[state] = bucket
        return self


class WindowsServiceCheck(StateMapCheck):
    pass


class UrlProbeCheck(_Strict):
    timeout_seconds: float = Field(gt=0)
    down_on_http_status: list[int]
    degraded_after_seconds: dict[str, float]

    @model_validator(mode="after")
    def _thresholds_below_timeout(self) -> UrlProbeCheck:
        for probe, secs in self.degraded_after_seconds.items():
            if not 0 < secs < self.timeout_seconds:
                raise ValueError(
                    f"degraded_after_seconds.{probe}={secs} must be > 0 and below timeout_seconds"
                )
        return self


class WeblogicServerCheck(StateMapCheck):
    stuck_thread_max_time_seconds: int = Field(gt=0)
    degraded_if_stuck_threads_above: int = Field(ge=0)
    degraded_if_heap_used_pct_above: Pct


class SqlServerCheck(_Strict):
    down_if_service_not_running: bool
    degraded_if_blocking_longer_than_minutes: float = Field(gt=0)
    degraded_if_deadlocks_in_15_min_above: int = Field(ge=0)
    degraded_if_log_used_pct_above: Pct
    degraded_if_tempdb_used_pct_above: Pct
    degraded_if_disk_free_pct_below: Pct
    down_if_disk_free_pct_below: Pct
    degraded_if_backup_failed_in_last_hours: float = Field(gt=0)
    degraded_if_agent_job_failed_in_last_hours: float = Field(gt=0)
    degraded_if_query_running_longer_than_minutes: float = Field(gt=0)

    @model_validator(mode="after")
    def _disk_order(self) -> SqlServerCheck:
        if self.down_if_disk_free_pct_below > self.degraded_if_disk_free_pct_below:
            raise ValueError("down_if_disk_free_pct_below must not exceed degraded_if_disk_free_pct_below")
        return self


class Checks(_Strict):
    windows_service: WindowsServiceCheck
    url_probe: UrlProbeCheck
    weblogic_server: WeblogicServerCheck
    sql_server: SqlServerCheck
    confirm_down_after_consecutive_failures: int = Field(ge=1)


CHECK_TYPES = ("windows_service", "url_probe", "weblogic_server", "sql_server")

# --------------------------------------------------------------------------- 2. components

class Component(_Strict):
    display_name: str = Field(min_length=1)
    nodes: list[str] = Field(min_length=1)
    checks: list[str] = Field(min_length=1)
    criticality: str
    enabled: bool = True
    hard_dependency: bool = False
    single_point_of_failure: bool = False
    lb_probe: bool = False

    @field_validator("nodes")
    @classmethod
    def _unique_nodes(cls, nodes: list[str]) -> list[str]:
        if len(set(nodes)) != len(nodes):
            raise ValueError("nodes must be unique")
        return nodes


# --------------------------------------------------------------------------- 3. scoring

class NodePoints(_Strict):
    up: Pct
    degraded: Pct
    down: Pct

    @model_validator(mode="after")
    def _ordered(self) -> NodePoints:
        if not self.up >= self.degraded >= self.down:
            raise ValueError("node_points must satisfy up >= degraded >= down")
        return self


class Bands(_Strict):
    green_min_pct: Pct
    amber_min_pct: Pct

    @model_validator(mode="after")
    def _ordered(self) -> Bands:
        if self.amber_min_pct > self.green_min_pct:
            raise ValueError("amber_min_pct must not exceed green_min_pct")
        return self


class Scoring(_Strict):
    node_points: NodePoints
    multi_node_mode: Literal["node_share"]
    overall: Literal["weighted_average"]
    # exclude:       an Unknown node is left out of its component's node share; a component with
    #                no known node is left out of the overall score.
    # count_as_down: an Unknown node scores and counts as Down.
    unknown_nodes: Literal["exclude", "count_as_down"]
    hard_dependency_cap_pct: Pct
    bands: Bands


# --------------------------------------------------------------------------- 4. notices etc.

class Notices(_Strict):
    channel: Literal["email"]
    first_notice_after_minutes: float = Field(gt=0)
    first_notice_after_minutes_close: float = Field(gt=0)
    repeat_every_minutes: float = Field(gt=0)
    escalate_to_manager_after_minutes: float = Field(gt=0)
    notify_on_degraded: bool
    send_recovery_notice: bool
    require_acknowledgement: bool
    group_downstream_under_root_cause: bool
    include_restart_guidance: bool
    auto_restart: Literal[False]  # hard rule 3: read-only against HFM
    owners_file: str


class CloseCalendar(_Strict):
    business_days_before_month_end: int = Field(ge=0)
    business_days_after_month_start: int = Field(ge=0)
    quarter_end_months: list[int]
    extra_dates: list[date]

    @field_validator("quarter_end_months")
    @classmethod
    def _months(cls, months: list[int]) -> list[int]:
        if any(not 1 <= m <= 12 for m in months):
            raise ValueError("quarter_end_months must be between 1 and 12")
        return months


class Maintenance(_Strict):
    suppress_notices_in_window: bool
    show_on_dashboard_as: str


class Collection(_Strict):
    poll_interval_seconds: int = Field(gt=0)
    store_times_in: Literal["UTC"]  # hard rule 5
    history_retention_days: int = Field(gt=0)
    history_views_enabled: bool


class EnvironmentRules(_Strict):
    notices_enabled: bool


# --------------------------------------------------------------------------- root

class Rules(_Strict):
    version: Literal[1]
    checks: Checks
    criticality_weights: dict[str, int]
    components: dict[str, Component] = Field(min_length=1)
    scoring: Scoring
    notices: Notices
    close_calendar: CloseCalendar
    maintenance: Maintenance
    collection: Collection
    environments: dict[str, EnvironmentRules] = Field(min_length=1)

    @field_validator("criticality_weights")
    @classmethod
    def _weights_in_range(cls, weights: dict[str, int]) -> dict[str, int]:
        if not weights:
            raise ValueError("criticality_weights must not be empty")
        for name, w in weights.items():
            if not 1 <= w <= 100:
                raise ValueError(f"criticality weight {name}={w} out of range 1..100")
        return weights

    @model_validator(mode="after")
    def _cross_references(self) -> Rules:
        if not any(c.enabled for c in self.components.values()):
            raise ValueError("no enabled components")
        probes = self.checks.url_probe.degraded_after_seconds
        for cid, comp in self.components.items():
            if comp.criticality not in self.criticality_weights:
                raise ValueError(
                    f"component {cid}: criticality {comp.criticality!r} not in criticality_weights"
                )
            for ref in comp.checks:
                kind, _, arg = ref.partition(":")
                if kind not in CHECK_TYPES:
                    raise ValueError(f"component {cid}: unknown check {ref!r}")
                if kind == "url_probe":
                    if arg not in probes:
                        raise ValueError(
                            f"component {cid}: {ref!r} needs a probe name from "
                            f"checks.url_probe.degraded_after_seconds {sorted(probes)}"
                        )
                elif arg:
                    raise ValueError(f"component {cid}: check {kind!r} takes no argument")
        return self

    def enabled_components(self) -> dict[str, Component]:
        return {cid: c for cid, c in self.components.items() if c.enabled}

    def weight_of(self, component_id: str) -> int:
        return self.criticality_weights[self.components[component_id].criticality]
