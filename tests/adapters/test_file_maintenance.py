"""FileMaintenanceCalendar: start-up strictness, re-read on change, last-good on a bad edit."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

import pytest

from hfm_dashboard.adapters.file_maintenance import FileMaintenanceCalendar
from hfm_dashboard.app.notice_tracker import NoticeTracker
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.config.maintenance_loader import MaintenanceError
from hfm_dashboard.domain.notices import NoticeKind
from tests.app.test_notice_tracker import MIN, NOW, STATUS, states_with_hfm2_down
from tests.conftest import FILLED

ENVS = {"PROD", "NONPROD"}


def window(env="PROD", component=None, start="2026-11-17T08:00:00Z", end="2026-11-17T12:00:00Z"):
    comp = f"\n    component: {component}" if component else ""
    return f'  - environment: {env}{comp}\n    start: "{start}"\n    end: "{end}"\n'


def write(path, *windows, bump=0):
    path.write_text("windows:\n" + "".join(windows) if windows else "windows: []\n", encoding="utf-8")
    if bump:                                    # make sure the change is visible even on coarse clocks
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump * 1_000_000_000))


@pytest.fixture
def path(tmp_path):
    return tmp_path / "maintenance.yaml"


def test_missing_file_means_no_windows(path, repo_rules):
    assert FileMaintenanceCalendar(path, repo_rules, ENVS).windows("PROD", NOW) == []


def test_invalid_file_at_start_refuses(path, repo_rules):
    write(path, window(env="UAT"))
    with pytest.raises(MaintenanceError, match="UAT"):
        FileMaintenanceCalendar(path, repo_rules, ENVS)


def test_windows_per_environment_and_not_yet_ended(path, repo_rules):
    write(path, window(), window(env="NONPROD"), window(start="2026-11-16T08:00:00Z", end="2026-11-16T09:00:00Z"))
    cal = FileMaintenanceCalendar(path, repo_rules, ENVS)
    (w,) = cal.windows("PROD", NOW)                              # the ended one is left out
    assert w.end == datetime(2026, 11, 17, 12, tzinfo=UTC)
    assert len(cal.windows("NONPROD", NOW)) == 1


def test_edit_is_picked_up_without_restart(path, repo_rules):
    cal = FileMaintenanceCalendar(path, repo_rules, ENVS)
    assert cal.windows("PROD", NOW) == []
    write(path, window(component="epmhfm"), bump=1)
    assert [w.component for w in cal.windows("PROD", NOW)] == ["epmhfm"]


def test_invalid_edit_keeps_previous_windows_and_logs(path, repo_rules, caplog):
    write(path, window())
    cal = FileMaintenanceCalendar(path, repo_rules, ENVS)
    write(path, window(end="2026-11-17T07:00:00Z"), bump=1)       # end before start
    with caplog.at_level(logging.ERROR):
        assert len(cal.windows("PROD", NOW)) == 1
        assert len(cal.windows("PROD", NOW)) == 1
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "previous 1 window(s) stay in force" in errors[0].getMessage()
    write(path, bump=2)                                            # fixed (now empty)
    assert cal.windows("PROD", NOW) == []


def test_deleted_file_clears_windows(path, repo_rules):
    write(path, window())
    cal = FileMaintenanceCalendar(path, repo_rules, ENVS)
    path.unlink()
    assert cal.windows("PROD", NOW) == []


def test_window_in_file_suppresses_notice(path, repo_rules):
    """End to end with the notice engine: HFM node 2 down for 20 min inside a window - no notice;
    after the window ends the timer restarts at its end."""
    env = parse_environments(FILLED, repo_rules).environments["PROD"]
    write(path, window(component="epmhfm", start="2026-11-17T08:30:00Z", end="2026-11-17T09:30:00Z"))
    tracker = NoticeTracker("PROD", repo_rules, maintenance=FileMaintenanceCalendar(path, repo_rules, ENVS))
    states = states_with_hfm2_down(env, repo_rules)
    assert tracker.process(NOW + 20 * MIN, states, STATUS) == []
    assert tracker.process(NOW + 40 * MIN, states, STATUS) == []          # 10 min after window end
    end = NOW + 30 * MIN
    first = tracker.process(end + timedelta(minutes=repo_rules.notices.first_notice_after_minutes), states, STATUS)
    assert [n.kind for n in first] == [NoticeKind.FIRST]
