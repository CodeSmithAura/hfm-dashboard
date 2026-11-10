"""environments.yaml loading: placeholder refusal, structure, consistency with rules.yaml.

The shipped template is filled with invented values (``*.example.test``, port 8443) so the tests
also prove that the template and rules.yaml agree with each other.
"""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest
import yaml

from hfm_dashboard.config.environment_loader import (
    EnvironmentConfigError,
    find_placeholders,
    load_environments,
    parse_environments,
)
from hfm_dashboard.config.startup import ConfigurationError, load_configuration
from hfm_dashboard.domain.environment import EnvironmentsConfig, check_against_rules
from tests.conftest import FILLED, RULES_PATH, TEMPLATE


@pytest.fixture
def env_data() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(FILLED))


def problems(data: dict[str, Any], rules) -> list[str]:
    return check_against_rules(EnvironmentsConfig.model_validate(data), rules)


# --------------------------------------------------------------------------- template

class TestTemplate:
    def test_filled_template_is_valid_against_shipped_rules(self, repo_rules):
        cfg = parse_environments(FILLED, repo_rules)
        assert set(cfg.environments) == {"PROD", "NONPROD"}

    def test_unfilled_template_is_refused_with_every_placeholder(self, repo_rules):
        with pytest.raises(EnvironmentConfigError) as exc:
            parse_environments(TEMPLATE, repo_rules)
        msg = str(exc.value)
        assert "not filled in" in msg
        for name in ("{{LB_URL}}", "{{DB_NODE_01}}", "{{HFM_NODE_03}}", "{{SQL_PORT}}",
                     "{{NP_WEB_NODE_01}}", "{{MONITOR_HOST}}"):
            assert name in msg
        assert "environments.PROD.nodes.db_node_01.host" in msg

    def test_template_has_no_real_values_left_unplaceheld(self):
        """Hosts and ports in the template must all be placeholders (hard rule 1)."""
        data = yaml.safe_load(TEMPLATE)
        for env in data["environments"].values():
            for node in env["nodes"].values():
                assert re.fullmatch(r"\{\{[A-Z0-9_]+\}\}", node["host"])
            for port in [*env["ports"].values(), env["winrm"]["port"]]:
                assert re.fullmatch(r"\{\{[A-Z0-9_]+\}\}", port)

    def test_winrm_settings(self, repo_rules):
        winrm = parse_environments(FILLED, repo_rules).environments["PROD"].winrm
        assert winrm.port == 8443 and winrm.auth == "negotiate" and winrm.cert_validation

    def test_production_service_names_differ_per_node(self, repo_rules):
        cfg = parse_environments(FILLED, repo_rules).environments["PROD"]
        names = {cfg.service_name("epmhfm", n) for n in cfg.nodes_of("epmhfm", repo_rules)}
        assert len(names) == 3

    def test_nonprod_monitors_only_its_nodes(self, repo_rules):
        cfg = parse_environments(FILLED, repo_rules).environments["NONPROD"]
        assert cfg.nodes_of("epmhfm", repo_rules) == ["hfm_node_01"]
        assert cfg.nodes_of("hfm_web", repo_rules) == ["web_node_01"]

    def test_ports_become_integers(self, repo_rules):
        cfg = parse_environments(FILLED, repo_rules).environments["PROD"]
        assert cfg.ports["sql"] == 8443


# --------------------------------------------------------------------------- placeholders

class TestPlaceholders:
    def test_placeholder_in_comment_is_ignored(self, repo_rules):
        parse_environments("# copy and fill every {{PLACEHOLDER}}\n" + FILLED, repo_rules)

    def test_single_leftover_named(self, repo_rules):
        text = FILLED.replace("hfm-node-02.example.test", "{{HFM_NODE_02}}", 1)
        with pytest.raises(EnvironmentConfigError, match=r"1 placeholder\(s\)") as exc:
            parse_environments(text, repo_rules)
        assert "environments.PROD.nodes.hfm_node_02.host: {{HFM_NODE_02}}" in str(exc.value)

    def test_placeholder_inside_longer_value(self):
        assert find_placeholders({"a": {"url": "https://{{LB_HOST}}:443/x"}}) == ["a.url: {{LB_HOST}}"]

    def test_placeholder_in_list_and_key(self):
        found = find_placeholders({"dbs": ["HFM", "{{DB}}"], "{{K}}": 1})
        assert "dbs[1]: {{DB}}" in found
        assert "{{K}} (key)" in found

    def test_placeholder_check_runs_before_structure(self, repo_rules):
        with pytest.raises(EnvironmentConfigError, match="not filled in"):
            parse_environments('environments: "{{X}}"\nbogus_key: 1\n', repo_rules)

    def test_unquoted_placeholder_gives_clear_error(self, repo_rules):
        with pytest.raises(EnvironmentConfigError, match="left unquoted"):
            parse_environments("monitoring:\n  monitor_host: {{MONITOR_HOST}}\n", repo_rules)


# --------------------------------------------------------------------------- loader basics

class TestLoader:
    def test_missing_file_hints_at_template(self, tmp_path, repo_rules):
        with pytest.raises(EnvironmentConfigError, match="environments.template.yaml"):
            load_environments(tmp_path / "environments.yaml", repo_rules)

    def test_load_from_file(self, tmp_path, repo_rules):
        path = tmp_path / "environments.yaml"
        path.write_text(FILLED, encoding="utf-8")
        assert load_environments(path, repo_rules).monitoring.monitor_host

    def test_bad_yaml(self, repo_rules):
        with pytest.raises(EnvironmentConfigError, match="not valid YAML"):
            parse_environments("a: [1", repo_rules)

    def test_duplicate_key(self, repo_rules):
        text = FILLED.replace("    nodes:\n", "    nodes:\n      db_node_01: { host: x, role: database }\n", 1)
        with pytest.raises(EnvironmentConfigError, match="duplicate key 'db_node_01'"):
            parse_environments(text, repo_rules)

    def test_structure_errors_by_path_without_values(self, env_data, repo_rules):
        env_data["environments"]["PROD"]["ports"]["sql"] = "99999"
        env_data["environments"]["PROD"]["nodes"]["web_node_01"]["role"] = "webby"
        env_data["environments"]["PROD"]["surprise"] = True
        with pytest.raises(EnvironmentConfigError) as exc:
            parse_environments(yaml.safe_dump(env_data), repo_rules)
        msg = str(exc.value)
        assert "3 problem(s)" in msg
        assert "environments.PROD.ports.sql" in msg
        assert "environments.PROD.nodes.web_node_01.role" in msg
        assert "environments.PROD.surprise" in msg
        assert "99999" not in msg and "webby" not in msg

    def test_lb_url_must_be_http(self, env_data, repo_rules):
        env_data["environments"]["PROD"]["load_balancer"]["url"] = "lb.example.test"
        with pytest.raises(EnvironmentConfigError, match="load_balancer.url"):
            parse_environments(yaml.safe_dump(env_data), repo_rules)

    def test_inconsistency_reported_as_against_rules(self, env_data, repo_rules):
        del env_data["environments"]["PROD"]["services"]["epmhfm"]["hfm_node_02"]
        with pytest.raises(EnvironmentConfigError, match="against rules.yaml"):
            parse_environments(yaml.safe_dump(env_data), repo_rules)


# --------------------------------------------------------------------------- cross-checks

class TestAgainstRules:
    def test_consistent(self, env_data, repo_rules):
        assert problems(env_data, repo_rules) == []

    def test_missing_service_name_for_a_node(self, env_data, repo_rules):
        del env_data["environments"]["PROD"]["services"]["epmhfm"]["hfm_node_03"]
        assert problems(env_data, repo_rules) == [
            "environments.PROD.services.epmhfm: no service name for node hfm_node_03"
        ]

    def test_missing_service_block_for_a_component(self, env_data, repo_rules):
        del env_data["environments"]["NONPROD"]["services"]["sql_agent"]
        assert problems(env_data, repo_rules) == [
            "environments.NONPROD.services.sql_agent: no service name for node db_node_01"
        ]

    def test_component_with_no_nodes_in_environment(self, env_data, repo_rules):
        env = env_data["environments"]["NONPROD"]
        del env["nodes"]["hfm_node_01"]
        del env["services"]["epmhfm"]
        found = problems(env_data, repo_rules)
        assert any("component epmhfm has none of its nodes" in p for p in found)

    def test_declared_node_unused_catches_typo(self, env_data, repo_rules):
        env = env_data["environments"]["PROD"]
        env["nodes"]["hfm_node3"] = env["nodes"].pop("hfm_node_03")
        env["services"]["epmhfm"]["hfm_node3"] = env["services"]["epmhfm"].pop("hfm_node_03")
        found = problems(env_data, repo_rules)
        assert "environments.PROD.nodes.hfm_node3: not used by any enabled component in rules.yaml" in found
        assert any("component epmhfm does not run on hfm_node3" in p for p in found)

    def test_service_for_unknown_component(self, env_data, repo_rules):
        env_data["environments"]["PROD"]["services"]["essbase"] = {"web_node_01": "Essbase"}
        assert problems(env_data, repo_rules) == [
            "environments.PROD.services.essbase: no such component in rules.yaml"
        ]

    def test_service_for_disabled_component_is_allowed(self, env_data, repo_rules):
        env_data["environments"]["PROD"]["services"]["fr_rmi"] = {"web_node_01": "FRRMI1"}
        assert problems(env_data, repo_rules) == []

    def test_service_on_undeclared_node(self, env_data, repo_rules):
        env_data["environments"]["NONPROD"]["services"]["epmhfm"]["hfm_node_02"] = "X"
        assert problems(env_data, repo_rules) == [
            "environments.NONPROD.services.epmhfm.hfm_node_02: node not declared in this environment"
        ]

    def test_service_on_node_component_does_not_use(self, env_data, repo_rules):
        env_data["environments"]["PROD"]["services"]["admin_server"]["web_node_02"] = "X"
        assert problems(env_data, repo_rules) == [
            "environments.PROD.services.admin_server.web_node_02: component admin_server does not "
            "run on web_node_02 in rules.yaml"
        ]

    def test_environment_not_in_rules(self, env_data, repo_rules):
        env_data["environments"]["UAT"] = copy.deepcopy(env_data["environments"]["NONPROD"])
        env_data["environments"]["UAT"].pop("notices_enabled")
        found = problems(env_data, repo_rules)
        assert len(found) == 1 and "environments.UAT: not listed under environments in rules.yaml" in found[0]

    def test_notices_enabled_must_agree_with_rules(self, env_data, repo_rules):
        env_data["environments"]["NONPROD"]["notices_enabled"] = True
        found = problems(env_data, repo_rules)
        assert len(found) == 1 and "rules.yaml decides" in found[0]

    def test_notices_enabled_optional(self, env_data, repo_rules):
        env_data["environments"]["PROD"].pop("notices_enabled")
        assert problems(env_data, repo_rules) == []

    def test_several_problems_all_listed(self, env_data, repo_rules):
        del env_data["environments"]["PROD"]["services"]["epmhfm"]["hfm_node_03"]
        env_data["environments"]["PROD"]["services"]["essbase"] = {"web_node_01": "Essbase"}
        assert len(problems(env_data, repo_rules)) == 2


# --------------------------------------------------------------------------- start-up gate

class TestStartup:
    def test_loads_both_files(self, tmp_path):
        (tmp_path / "rules.yaml").write_text(RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        (tmp_path / "environments.yaml").write_text(FILLED, encoding="utf-8")
        cfg = load_configuration(tmp_path)
        assert cfg.rules.version == 1
        assert "PROD" in cfg.environments.environments

    def test_refuses_on_placeholders(self, tmp_path):
        (tmp_path / "rules.yaml").write_text(RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        (tmp_path / "environments.yaml").write_text(TEMPLATE, encoding="utf-8")
        with pytest.raises(ConfigurationError, match="(?s)refusing to start.*placeholder"):
            load_configuration(tmp_path)

    def test_refuses_on_invalid_rules(self, tmp_path):
        bad = RULES_PATH.read_text(encoding="utf-8").replace("auto_restart: false", "auto_restart: true")
        (tmp_path / "rules.yaml").write_text(bad, encoding="utf-8")
        (tmp_path / "environments.yaml").write_text(FILLED, encoding="utf-8")
        with pytest.raises(ConfigurationError, match="(?s)refusing to start.*auto_restart"):
            load_configuration(tmp_path)

    def test_refuses_when_environments_missing(self, tmp_path):
        (tmp_path / "rules.yaml").write_text(RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="cannot read environments.yaml"):
            load_configuration(tmp_path)
