"""Scoring maths against the synthetic rules in conftest.

Weights: db 3 (hard dep), lb 3 (hard dep, 2 nodes), app 3 (3 nodes), admin 1 -> total 10.
"""

import pytest

from hfm_dashboard.domain.scoring import Band, band_for, score
from hfm_dashboard.domain.status import Status

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN


def all_up(**overrides):
    statuses = {
        "db": {"db1": U},
        "lb": {"web1": U, "web2": U},
        "app": {"app1": U, "app2": U, "app3": U},
        "admin": {"web1": U},
    }
    for cid, nodes in overrides.items():
        statuses[cid] = {**statuses[cid], **nodes}
    return statuses


def test_all_up_is_100_green(rules):
    result = score(all_up(), rules)
    assert result.overall_pct == 100
    assert result.band is Band.GREEN
    assert result.capped_by == ()
    assert {c.status for c in result.components.values()} == {U}


def test_disabled_component_is_not_scored(rules):
    assert "retired" not in score(all_up(), rules).components


class TestNodeShare:
    def test_one_of_three_down(self, rules):
        """CONFIRMED Q27: 2 of 3 up = 67% for the component, component Degraded."""
        result = score(all_up(app={"app2": D}), rules)
        app = result.components["app"]
        assert app.score_pct == pytest.approx(200 / 3)
        assert app.status is G
        # overall = (3*100 + 3*100 + 3*66.67 + 1*100) / 10
        assert result.overall_pct == pytest.approx(90.0)
        assert result.band is Band.AMBER

    def test_degraded_node_counts_half(self, rules):
        app = score(all_up(app={"app1": G}), rules).components["app"]
        assert app.score_pct == pytest.approx(250 / 3)
        assert app.status is G

    def test_all_nodes_down_is_down(self, rules):
        app = score(all_up(app={"app1": D, "app2": D, "app3": D}), rules).components["app"]
        assert app.score_pct == 0
        assert app.status is D

    def test_all_nodes_degraded_is_degraded(self, rules):
        app = score(all_up(app={"app1": G, "app2": G, "app3": G}), rules).components["app"]
        assert app.score_pct == 50
        assert app.status is G

    def test_node_points_come_from_rules(self, build_rules):
        rules = build_rules(node_points={"up": 100, "degraded": 80, "down": 10})
        app = score(all_up(app={"app1": G, "app2": D}), rules).components["app"]
        assert app.score_pct == pytest.approx((100 + 80 + 10) / 3)


class TestHardDependency:
    def test_single_node_hard_dependency_down_caps_overall(self, rules):
        result = score(all_up(db={"db1": D}), rules)
        assert result.uncapped_pct == pytest.approx(70.0)
        assert result.overall_pct == 0
        assert result.capped_by == ("db",)
        assert result.band is Band.RED

    def test_partial_multi_node_hard_dependency_does_not_cap(self, rules):
        """OHS/LB caps only when ALL nodes are down (rules.yaml comment, ASSUMED Q28)."""
        result = score(all_up(lb={"web1": D}), rules)
        assert result.capped_by == ()
        assert result.overall_pct == pytest.approx(85.0)

    def test_all_nodes_of_multi_node_hard_dependency_down_caps(self, rules):
        result = score(all_up(lb={"web1": D, "web2": D}), rules)
        assert result.capped_by == ("lb",)
        assert result.overall_pct == 0

    def test_degraded_hard_dependency_does_not_cap(self, rules):
        result = score(all_up(db={"db1": G}), rules)
        assert result.capped_by == ()
        assert result.overall_pct == pytest.approx(85.0)

    def test_several_hard_dependencies_down_are_all_reported(self, rules):
        result = score(all_up(db={"db1": D}, lb={"web1": D, "web2": D}), rules)
        assert result.capped_by == ("db", "lb")

    def test_down_non_hard_dependency_does_not_cap(self, rules):
        result = score(all_up(app={"app1": D, "app2": D, "app3": D}), rules)
        assert result.capped_by == ()
        assert result.overall_pct == pytest.approx(70.0)

    def test_cap_value_comes_from_rules(self, build_rules):
        rules = build_rules(hard_dependency_cap_pct=20)
        assert score(all_up(db={"db1": D}), rules).overall_pct == 20

    def test_cap_never_raises_the_score(self, build_rules):
        rules = build_rules(hard_dependency_cap_pct=90)
        result = score(all_up(db={"db1": D}, app={"app1": D, "app2": D, "app3": D}), rules)
        assert result.uncapped_pct == pytest.approx(40.0)
        assert result.overall_pct == pytest.approx(40.0)


class TestUnknown:
    def test_missing_node_is_unknown(self, rules):
        statuses = all_up()
        del statuses["app"]["app3"]
        assert score(statuses, rules).components["app"].nodes["app3"] is X

    def test_exclude_leaves_unknown_out_of_node_share(self, rules):
        result = score(all_up(app={"app3": X}), rules)
        assert result.components["app"].score_pct == 100
        assert result.overall_pct == 100

    def test_exclude_with_known_down_node(self, rules):
        app = score(all_up(app={"app1": D, "app2": U, "app3": X}), rules).components["app"]
        assert app.score_pct == 50
        assert app.status is G

    def test_component_with_no_known_node_is_left_out_of_overall(self, rules):
        result = score(all_up(app={"app1": X, "app2": X, "app3": X}), rules)
        app = result.components["app"]
        assert app.status is X and app.score_pct is None
        assert result.overall_pct == 100

    def test_unknown_hard_dependency_does_not_cap(self, rules):
        result = score(all_up(db={"db1": X}), rules)
        assert result.capped_by == ()

    def test_nothing_known_gives_unknown_band(self, rules):
        result = score({}, rules)
        assert result.overall_pct is None
        assert result.band is Band.UNKNOWN

    def test_count_as_down(self, build_rules):
        rules = build_rules(unknown_nodes="count_as_down")
        result = score(all_up(app={"app3": X}), rules)
        assert result.components["app"].score_pct == pytest.approx(200 / 3)

    def test_count_as_down_can_trigger_cap(self, build_rules):
        rules = build_rules(unknown_nodes="count_as_down")
        result = score(all_up(db={"db1": X}), rules)
        assert result.components["db"].status is D
        assert result.capped_by == ("db",)


class TestBands:
    @pytest.mark.parametrize(
        ("pct", "band"),
        [(100, Band.GREEN), (95, Band.GREEN), (94.99, Band.AMBER), (70, Band.AMBER),
         (69.99, Band.RED), (0, Band.RED), (None, Band.UNKNOWN)],
    )
    def test_boundaries_are_inclusive_minimums(self, rules, pct, band):
        assert band_for(pct, rules) is band

    def test_bands_come_from_rules(self, build_rules):
        rules = build_rules(bands={"green_min_pct": 99, "amber_min_pct": 90})
        assert band_for(98, rules) is Band.AMBER
        assert band_for(89, rules) is Band.RED

    def test_overall_is_rounded_so_float_noise_cannot_flip_a_band(self, build_rules):
        # app = 200/3 (weight 3), rest 100 (weight 7): exactly 90 in maths. With the green
        # threshold set at 90 the float result must still band Green.
        rules = build_rules(bands={"green_min_pct": 90, "amber_min_pct": 70})
        result = score(all_up(app={"app1": D}), rules)
        assert result.overall_pct == 90.0
        assert result.band is Band.GREEN


class TestWiringErrors:
    def test_unmonitored_component_rejected(self, rules):
        with pytest.raises(ValueError, match="unmonitored component 'nope'"):
            score({"nope": {"x": U}}, rules)

    def test_disabled_component_rejected(self, rules):
        with pytest.raises(ValueError, match="retired"):
            score({"retired": {"web1": U}}, rules)

    def test_unknown_node_rejected(self, rules):
        with pytest.raises(ValueError, match="unknown nodes"):
            score({"db": {"db1": U, "db9": U}}, rules)


def test_score_is_pure(rules):
    statuses = all_up(app={"app1": D})
    first = score(statuses, rules)
    assert score(statuses, rules) == first
    assert statuses["app"]["app1"] is D
