"""owners.yaml loading: missing file allowed, present file strict (invented addresses only)."""

from __future__ import annotations

import pytest

from hfm_dashboard.config.owners_loader import OwnersError, load_owners, parse_owners
from tests.conftest import REPO_ROOT

TEMPLATE = (REPO_ROOT / "config" / "owners.template.yaml").read_text(encoding="utf-8")


def test_template_is_valid(repo_rules):
    owners = parse_owners(TEMPLATE, repo_rules)
    assert owners.contacts_for("epmhfm").owners == ["hfm-team@example.test"]
    assert owners.contacts_for("fr_web").owners == ["epm-support@example.test"]     # default
    assert owners.monitoring_team == ["monitoring-team@example.test"]


def test_template_uses_only_invented_addresses():
    import re
    addresses = re.findall(r"[\w.-]+@[\w.-]+", TEMPLATE)
    assert addresses and all(a.endswith("@example.test") for a in addresses)


def test_missing_file_is_none(tmp_path, repo_rules):
    assert load_owners(tmp_path, repo_rules) is None


def test_load_from_file(tmp_path, repo_rules):
    (tmp_path / "owners.yaml").write_text(TEMPLATE, encoding="utf-8")
    assert load_owners(tmp_path, repo_rules).default is not None


def test_bad_address_reported_by_path_only(repo_rules):
    text = TEMPLATE.replace('owners: ["hfm-team@example.test"]', 'owners: ["hfm team at example"]')
    with pytest.raises(OwnersError) as info:
        parse_owners(text, repo_rules)
    assert "components.epmhfm.owners.0" in str(info.value) and "hfm team" not in str(info.value)


def test_unknown_component(repo_rules):
    text = TEMPLATE + '  essbase:\n    owners: ["x@example.test"]\n'
    with pytest.raises(OwnersError, match="components.essbase: no such component"):
        parse_owners(text, repo_rules)


def test_without_default_every_enabled_component_needed(repo_rules):
    text = 'monitoring_team: ["m@example.test"]\ncomponents:\n  epmhfm: {owners: ["h@example.test"]}\n'
    with pytest.raises(OwnersError, match="no owners for enabled component sql_server"):
        parse_owners(text, repo_rules)


def test_placeholders_refused(repo_rules):
    with pytest.raises(OwnersError, match="placeholder"):
        parse_owners('monitoring_team: ["{{MONITORING_MAILBOX}}"]\n', repo_rules)


def test_monitoring_team_required(repo_rules):
    with pytest.raises(OwnersError, match="monitoring_team"):
        parse_owners('default: {owners: ["x@example.test"]}\n', repo_rules)


def test_unknown_key(repo_rules):
    with pytest.raises(OwnersError, match="pagers"):
        parse_owners(TEMPLATE + 'pagers: []\n', repo_rules)
