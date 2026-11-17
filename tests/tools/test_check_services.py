"""check_services command: config loading, output, credential guard, masked recording."""

from __future__ import annotations

import io
import json

import pytest
import yaml

from hfm_dashboard.tools.check_services import main
from tests.conftest import FILLED, RULES_PATH, T0, TEMPLATE

NP_WEB = "np-web-node-01.example.test"
NP_DB = "np-db-node-01.example.test"


class FakeSource:
    """Stands in for WinRMServiceSource; emits output in the real script's JSON format."""

    instances: list["FakeSource"] = []

    def __init__(self, settings, timeout_seconds, credential=None, on_output=None, *,
                 stopped=(), failing_hosts=(), extra_text=""):
        self.settings, self.timeout, self.credential, self.on_output = (
            settings, timeout_seconds, credential, on_output)
        self.stopped, self.failing_hosts, self.extra_text = set(stopped), set(failing_hosts), extra_text
        FakeSource.instances.append(self)

    def query(self, host, names):
        from hfm_dashboard.ports.windows_services import ServiceQueryError
        if host in self.failing_hosts:
            raise ServiceQueryError("WinRM not reachable (ConnectionError)")
        states = {n: ("Stopped" if n in self.stopped else "Running") for n in names}
        output = json.dumps([{"wanted": n, "status": s} for n, s in states.items()]) + self.extra_text
        if self.on_output:
            self.on_output(host, output)
        return states


def factory(**kw):
    def make(settings, timeout_seconds, credential=None, on_output=None):
        return FakeSource(settings, timeout_seconds, credential, on_output, **kw)
    return make


@pytest.fixture
def config_dir(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    (d / "rules.yaml").write_text(RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (d / "environments.yaml").write_text(FILLED, encoding="utf-8")
    return d


def run(args, **kw):
    out, err = io.StringIO(), io.StringIO()
    kw.setdefault("source_factory", factory())
    code = main(args, out=out, err=err, now_fn=lambda: T0, **kw)
    return code, out.getvalue(), err.getvalue()


class TestRun:
    def test_all_up(self, config_dir):
        code, out, _ = run(["--env", "NONPROD", "--config-dir", str(config_dir)])
        assert code == 0
        assert "9 service check(s), 0 not Up" in out
        assert "epmhfm" in out and "hfm_node_01" in out

    def test_output_shows_node_keys_not_hosts(self, config_dir):
        _, out, _ = run(["--env", "NONPROD", "--config-dir", str(config_dir)])
        assert "example.test" not in out

    def test_not_up_exit_code(self, config_dir):
        code, out, _ = run(["--env", "NONPROD", "--config-dir", str(config_dir)],
                           source_factory=factory(stopped={"SQLSERVERAGENT"}))
        assert code == 1
        assert "1 not Up" in out
        assert "DOWN" in out

    def test_unreachable_host_is_unknown(self, config_dir):
        code, out, _ = run(["--env", "NONPROD", "--config-dir", str(config_dir)],
                           source_factory=factory(failing_hosts={NP_DB}))
        assert code == 1
        assert "UNKNOWN" in out and "WinRM not reachable" in out

    def test_uses_rules_timeout_and_env_winrm(self, config_dir):
        FakeSource.instances.clear()
        run(["--env", "NONPROD", "--config-dir", str(config_dir)])
        src = FakeSource.instances[-1]
        assert src.timeout == 20 and src.settings.port == 8443 and src.credential is None


class TestConfiguration:
    def test_only_chosen_environment_must_be_filled(self, config_dir):
        """PROD (and monitor_host) may keep placeholders when checking NONPROD."""
        data = yaml.safe_load(TEMPLATE)
        filled_np = yaml.safe_load(FILLED)["environments"]["NONPROD"]
        data["environments"]["NONPROD"] = filled_np
        (config_dir / "environments.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        code, _, err = run(["--env", "NONPROD", "--config-dir", str(config_dir)])
        assert code == 0, err

    def test_placeholder_in_chosen_environment_refused(self, config_dir):
        (config_dir / "environments.yaml").write_text(TEMPLATE, encoding="utf-8")
        code, _, err = run(["--env", "NONPROD", "--config-dir", str(config_dir)])
        assert code == 2
        assert "{{NP_WEB_NODE_01}}" in err and "{{DB_NODE_01}}" not in err

    def test_unknown_environment(self, config_dir):
        code, _, err = run(["--env", "UAT", "--config-dir", str(config_dir)])
        assert code == 2 and "'UAT' not found" in err

    def test_missing_config(self, tmp_path):
        code, _, err = run(["--env", "NONPROD", "--config-dir", str(tmp_path)])
        assert code == 2 and "configuration problem" in err


class TestCredential:
    def test_username_prompts_and_is_passed(self, config_dir):
        FakeSource.instances.clear()
        prompts = []
        code, _, _ = run(["--env", "NONPROD", "--config-dir", str(config_dir), "--username", "CORP\\dev.user"],
                         getpass_fn=lambda p: prompts.append(p) or "pw-123")
        assert code == 0
        assert prompts == ["Password for CORP\\dev.user: "]
        cred = FakeSource.instances[-1].credential
        assert cred.username == "CORP\\dev.user" and cred.password == "pw-123"

    def test_username_refused_for_prod(self, config_dir):
        code, _, err = run(["--env", "PROD", "--config-dir", str(config_dir), "--username", "x"],
                           getpass_fn=lambda p: pytest.fail("must not prompt"))
        assert code == 2 and "D11" in err


class TestRecord:
    def test_writes_masked_files_per_node(self, config_dir, tmp_path):
        rec = tmp_path / "rec"
        code, out, _ = run(["--env", "NONPROD", "--config-dir", str(config_dir), "--record", str(rec)])
        assert code == 0
        names = sorted(p.name for p in rec.iterdir())
        assert names == ["nonprod_db_node_01.json", "nonprod_hfm_node_01.json",
                         "nonprod_summary.txt", "nonprod_web_node_01.json"]
        rows = json.loads((rec / "nonprod_web_node_01.json").read_text(encoding="utf-8"))
        assert {r["status"] for r in rows} == {"Running"} and len(rows) == 6
        assert "review them" in out

    def test_hosts_users_and_ips_masked(self, config_dir, tmp_path):
        rec = tmp_path / "rec"
        noise = f" host {NP_WEB} np-web-node-01 user CORP\\dev.user ip 203.0.113.3"
        run(["--env", "NONPROD", "--config-dir", str(config_dir), "--record", str(rec),
             "--username", "CORP\\dev.user"],
            source_factory=factory(extra_text=noise), getpass_fn=lambda p: "pw")
        text = (rec / "nonprod_web_node_01.json").read_text(encoding="utf-8")
        assert "dev.user" not in text and "CORP" not in text and "203.0.113.3" not in text
        assert "monitor-user" in text and "<ip-masked>" in text

    def test_real_host_replaced_by_node_based_name(self, config_dir, tmp_path):
        data = yaml.safe_load(FILLED)
        data["environments"]["NONPROD"]["nodes"]["web_node_01"]["host"] = "CLWEB9.client-corp.invalid"
        (config_dir / "environments.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        rec = tmp_path / "rec"
        run(["--env", "NONPROD", "--config-dir", str(config_dir), "--record", str(rec)],
            source_factory=factory(extra_text=" seen on CLWEB9.client-corp.invalid and clweb9"))
        text = (rec / "nonprod_web_node_01.json").read_text(encoding="utf-8")
        assert "client-corp" not in text.lower() and "clweb9" not in text.lower()
        assert "web-node-01.example.test" in text

    def test_nothing_written_if_masking_misses_something(self, config_dir, tmp_path, monkeypatch):
        import hfm_dashboard.tools.check_services as cli
        monkeypatch.setattr(cli, "mask", lambda text, pairs: text)   # simulate a masking gap
        rec = tmp_path / "rec"
        code, _, err = run(["--env", "NONPROD", "--config-dir", str(config_dir), "--record", str(rec)],
                           source_factory=factory(extra_text=" 203.0.113.7"))
        assert code == 2
        assert "not recorded" in err and "IPv4 address" in err and "203.0.113.7" not in err
        assert not rec.exists()

    def test_failed_host_has_no_raw_file(self, config_dir, tmp_path):
        rec = tmp_path / "rec"
        run(["--env", "NONPROD", "--config-dir", str(config_dir), "--record", str(rec)],
            source_factory=factory(failing_hosts={NP_DB}))
        assert not (rec / "nonprod_db_node_01.json").exists()
        assert "WinRM not reachable" in (rec / "nonprod_summary.txt").read_text(encoding="utf-8")
