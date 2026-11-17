"""Masking of recorded output (hard rule 1). Hosts here are invented, like every test value."""

from __future__ import annotations

import pytest

from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.tools.masking import IP_MASK, build_masks, leftovers, mask


def env_with(hosts: dict[str, str]) -> Environment:
    return Environment.model_validate({
        "display_name": "Test",
        "load_balancer": {"url": "https://lb.client-corp.invalid", "paths": {}},
        "nodes": {k: {"host": h, "role": "web"} for k, h in hosts.items()},
        "ports": {},
        "winrm": {"port": 1234, "use_ssl": False, "auth": "negotiate"},
        "weblogic": {"admin_url": "https://wlsadm.client-corp.invalid:1234", "servers": {}},
        "url_probes": {"scheme": "http", "probes": {}},
        "sql": {"port": "sql", "auth": "windows"},
        "services": {},
        "databases": [],
        "notices": {"smtp_relay": "relay.client-corp.invalid", "from_address": "a@client-corp.invalid"},
        "secrets": {},
    })


@pytest.fixture
def pairs():
    env = env_with({"web_node_01": "CLWEB01.client-corp.invalid", "db_node_01": "cldb01.client-corp.invalid"})
    return build_masks(env, [("CORP\\jdoe", "monitor-user")])


class TestMask:
    def test_fqdn_short_name_and_suffix(self, pairs):
        text = "CLWEB01.client-corp.invalid / clweb01 / host.client-corp.invalid / CLDB01"
        masked = mask(text, pairs)
        assert masked == "web-node-01.example.test / web-node-01 / host.example.test / db-node-01"

    def test_case_insensitive(self, pairs):
        assert mask("ClWeB01.CLIENT-CORP.invalid", pairs) == "web-node-01.example.test"

    def test_short_name_inside_longer_token_untouched(self, pairs):
        assert mask("HyS9FRReports_CLWEB01X clweb01-old", pairs) == "HyS9FRReports_CLWEB01X clweb01-old"

    def test_extra_values(self, pairs):
        assert mask("logon CORP\\jdoe failed", pairs) == "logon monitor-user failed"

    def test_ip_addresses(self, pairs):
        assert mask("from 192.0.2.40:1234 and 198.51.100.5", pairs) == f"from {IP_MASK}:1234 and {IP_MASK}"

    def test_version_like_numbers_not_ip(self, pairs):
        assert mask("EPM 11.2.25 and 1.2.3.4.5", pairs) == "EPM 11.2.25 and 1.2.3.4.5"

    def test_service_json_unchanged(self, pairs):
        text = '[{"wanted":"HyS9FRReports_EPMWEB1","status":"Running"}]'
        assert mask(text, pairs) == text

    def test_url_hosts_masked(self, pairs):
        assert mask("lb.client-corp.invalid wlsadm.client-corp.invalid wlsadm", pairs) == \
            "lb.example.test wls-admin.example.test wls-admin"

    def test_already_invented_values_not_masked(self):
        pairs = build_masks(env_with({"web_node_01": "web-node-01.example.test"}))
        assert not any("example.test" in real or real == "web-node-01" for real, _ in pairs)
        assert leftovers("web-node-01.example.test", pairs) == []

    def test_ip_as_host(self):
        pairs = build_masks(env_with({"web_node_01": "203.0.113.3"}))
        assert mask("203.0.113.3", pairs) == "web-node-01.example.test"


class TestLeftovers:
    def test_clean_after_masking(self, pairs):
        text = "CLWEB01.client-corp.invalid 192.0.2.1 CORP\\jdoe"
        assert leftovers(mask(text, pairs), pairs) == []

    def test_reports_kinds_not_values(self, pairs):
        found = leftovers("clweb01 at 192.0.2.1", pairs)
        assert "IPv4 address" in found
        assert all("clweb01" not in f.lower() for f in found)
