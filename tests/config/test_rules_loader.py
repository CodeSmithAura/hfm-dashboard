"""Loading the shipped config/rules.yaml, and behaviour pinned to its current values.

The TestRepoRules expectations follow the CONFIRMED / ASSUMED values in rules.yaml. When the
application team changes a value, update the matching expectation here.
"""

import pytest

from hfm_dashboard.config.rules_loader import RulesError, load_rules, parse_rules
from hfm_dashboard.domain.scoring import Band, score
from hfm_dashboard.domain.status import Status
from tests.conftest import RULES_PATH

U, G, D = Status.UP, Status.DEGRADED, Status.DOWN


class TestLoader:
    def test_shipped_rules_load(self, repo_rules):
        assert repo_rules.version == 1

    def test_missing_file(self, tmp_path):
        with pytest.raises(RulesError, match="cannot read"):
            load_rules(tmp_path / "rules.yaml")

    def test_bad_yaml(self):
        with pytest.raises(RulesError, match="not valid YAML"):
            parse_rules("a: [1, 2")

    def test_top_level_not_mapping(self):
        with pytest.raises(RulesError, match="mapping"):
            parse_rules("- 1\n- 2\n")

    def test_duplicate_key_rejected(self):
        with pytest.raises(RulesError, match="duplicate key 'epmhfm'"):
            parse_rules("components:\n  epmhfm: {}\n  epmhfm: {}\n")

    def test_validation_errors_are_listed_by_path(self):
        text = RULES_PATH.read_text(encoding="utf-8").replace(
            "hard_dependency_cap_pct: 0", "hard_dependency_cap_pct: 150"
        )
        with pytest.raises(RulesError, match=r"scoring\.hard_dependency_cap_pct"):
            parse_rules(text)

    def test_unknown_key_in_shipped_file_is_rejected(self):
        text = RULES_PATH.read_text(encoding="utf-8").replace(
            "  overall: weighted_average", "  overal: weighted_average"
        )
        with pytest.raises(RulesError, match="overal"):
            parse_rules(text)


class TestRepoRules:
    """Agreed behaviour with the current rules.yaml."""

    @pytest.fixture
    def all_up(self, repo_rules):
        return {cid: {n: U for n in c.nodes} for cid, c in repo_rules.enabled_components().items()}

    def test_monitored_components(self, repo_rules):
        assert set(repo_rules.enabled_components()) == {
            "sql_server", "sql_agent", "shared_services", "ohs_load_balancer", "epmhfm",
            "hfm_web", "fr_web", "admin_server", "ohs_node_manager",
        }
        assert repo_rules.components["fr_rmi"].enabled is False   # Q07: does not exist

    def test_hard_dependencies(self, repo_rules):
        hard = {cid for cid, c in repo_rules.enabled_components().items() if c.hard_dependency}
        assert hard == {"sql_server", "shared_services", "ohs_load_balancer"}   # ASSUMED Q28

    def test_flap_protection(self, repo_rules):
        assert repo_rules.checks.confirm_down_after_consecutive_failures == 2   # ASSUMED Q40

    def test_all_up(self, repo_rules, all_up):
        result = score(all_up, repo_rules)
        assert (result.overall_pct, result.band) == (100, Band.GREEN)

    def test_one_hfm_node_down(self, repo_rules, all_up):
        """Q27 example: 2 of 3 EPMHFM up = 67% for the component."""
        all_up["epmhfm"]["hfm_node_02"] = D
        result = score(all_up, repo_rules)
        assert result.components["epmhfm"].score_pct == pytest.approx(66.67, abs=0.01)
        assert result.components["epmhfm"].status is G
        # total weight 25; loss 4 * 33.33 / 25
        assert result.overall_pct == pytest.approx(94.67)
        assert result.band is Band.AMBER

    def test_sql_server_down_caps_to_zero(self, repo_rules, all_up):
        all_up["sql_server"]["db_node_01"] = D
        result = score(all_up, repo_rules)
        assert result.overall_pct == 0
        assert result.capped_by == ("sql_server",)
        assert result.band is Band.RED

    def test_one_ohs_node_down_does_not_cap(self, repo_rules, all_up):
        all_up["ohs_load_balancer"]["web_node_01"] = D
        result = score(all_up, repo_rules)
        assert result.capped_by == ()
        assert result.overall_pct == pytest.approx(92.0)

    def test_both_ohs_nodes_down_caps(self, repo_rules, all_up):
        all_up["ohs_load_balancer"] = {"web_node_01": D, "web_node_02": D}
        assert score(all_up, repo_rules).overall_pct == 0

    def test_admin_server_down_stays_green(self, repo_rules, all_up):
        all_up["admin_server"]["web_node_01"] = D
        result = score(all_up, repo_rules)
        assert result.overall_pct == pytest.approx(96.0)
        assert result.band is Band.GREEN
