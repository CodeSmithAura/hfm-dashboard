"""rules.yaml validation: the program must refuse to start on an invalid file (hard rule 6)."""

import pytest
from pydantic import ValidationError

from hfm_dashboard.domain.rules import Rules


def invalid(rules_dict, match):
    with pytest.raises(ValidationError, match=match):
        Rules.model_validate(rules_dict)


def test_synthetic_rules_are_valid(rules_dict):
    Rules.model_validate(rules_dict)


class TestUnknownAndMissingKeys:
    @pytest.mark.parametrize(
        "path",
        [(), ("checks",), ("checks", "windows_service"), ("scoring",), ("scoring", "bands"),
         ("components", "db"), ("notices",), ("collection",), ("environments", "PROD")],
    )
    def test_unknown_key_rejected_everywhere(self, rules_dict, path):
        target = rules_dict
        for key in path:
            target = target[key]
        target["typo_key"] = 1
        invalid(rules_dict, "typo_key")

    @pytest.mark.parametrize(
        ("section", "key"),
        [("scoring", "hard_dependency_cap_pct"), ("scoring", "unknown_nodes"),
         ("scoring", "node_points"), ("checks", "confirm_down_after_consecutive_failures"),
         ("notices", "first_notice_after_minutes")],
    )
    def test_required_key_missing(self, rules_dict, section, key):
        del rules_dict[section][key]
        invalid(rules_dict, key)

    def test_no_components(self, rules_dict):
        rules_dict["components"] = {}
        invalid(rules_dict, "components")

    def test_no_enabled_components(self, rules_dict):
        for comp in rules_dict["components"].values():
            comp["enabled"] = False
        invalid(rules_dict, "no enabled components")

    def test_component_without_nodes(self, rules_dict):
        rules_dict["components"]["db"]["nodes"] = []
        invalid(rules_dict, "nodes")

    def test_duplicate_node(self, rules_dict):
        rules_dict["components"]["lb"]["nodes"] = ["web1", "web1"]
        invalid(rules_dict, "unique")


class TestWeights:
    @pytest.mark.parametrize("weight", [0, -1, 101])
    def test_weight_out_of_range(self, rules_dict, weight):
        rules_dict["criticality_weights"]["low"] = weight
        invalid(rules_dict, "out of range")

    def test_undefined_criticality(self, rules_dict):
        rules_dict["components"]["admin"]["criticality"] = "medium"
        invalid(rules_dict, "criticality 'medium' not in criticality_weights")

    def test_weight_of(self, rules):
        assert rules.weight_of("db") == 3
        assert rules.weight_of("admin") == 1


class TestScoringSection:
    @pytest.mark.parametrize("pts", [{"up": 100, "degraded": 50, "down": 60},
                                     {"up": 40, "degraded": 50, "down": 0},
                                     {"up": 120, "degraded": 50, "down": 0}])
    def test_node_points_invalid(self, rules_dict, pts):
        rules_dict["scoring"]["node_points"] = pts
        invalid(rules_dict, "node_points")

    def test_bands_out_of_order(self, rules_dict):
        rules_dict["scoring"]["bands"] = {"green_min_pct": 60, "amber_min_pct": 70}
        invalid(rules_dict, "amber_min_pct")

    @pytest.mark.parametrize("cap", [-1, 101])
    def test_cap_out_of_range(self, rules_dict, cap):
        rules_dict["scoring"]["hard_dependency_cap_pct"] = cap
        invalid(rules_dict, "hard_dependency_cap_pct")

    @pytest.mark.parametrize(("key", "value"), [("multi_node_mode", "best_node"),
                                                ("overall", "minimum"),
                                                ("unknown_nodes", "count_as_up")])
    def test_unsupported_modes(self, rules_dict, key, value):
        rules_dict["scoring"][key] = value
        invalid(rules_dict, key)


class TestCheckReferences:
    def test_unknown_check_type(self, rules_dict):
        rules_dict["components"]["db"]["checks"] = ["ping"]
        invalid(rules_dict, "unknown check 'ping'")

    def test_url_probe_needs_known_probe(self, rules_dict):
        rules_dict["components"]["app"]["checks"] = ["url_probe:nope"]
        invalid(rules_dict, "url_probe:nope")

    def test_url_probe_without_name(self, rules_dict):
        rules_dict["components"]["app"]["checks"] = ["url_probe"]
        invalid(rules_dict, "probe name")

    def test_argument_on_plain_check(self, rules_dict):
        rules_dict["components"]["db"]["checks"] = ["windows_service:x"]
        invalid(rules_dict, "takes no argument")

    def test_state_in_two_buckets(self, rules_dict):
        rules_dict["checks"]["windows_service"]["degraded"] = ["Running"]
        invalid(rules_dict, "Running")

    def test_confirm_down_must_be_positive(self, rules_dict):
        rules_dict["checks"]["confirm_down_after_consecutive_failures"] = 0
        invalid(rules_dict, "confirm_down_after_consecutive_failures")


class TestHardRulesEnforced:
    def test_auto_restart_must_stay_false(self, rules_dict):
        rules_dict["notices"]["auto_restart"] = True
        invalid(rules_dict, "auto_restart")

    def test_times_must_be_utc(self, rules_dict):
        rules_dict["collection"]["store_times_in"] = "local"
        invalid(rules_dict, "store_times_in")
