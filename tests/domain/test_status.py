from datetime import datetime, timedelta, timezone

import pytest

from hfm_dashboard.domain.status import CheckResult, NodeState, Status, advance, worst
from tests.conftest import T0

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN
POLL = timedelta(seconds=60)


class TestWorst:
    @pytest.mark.parametrize(
        ("checks", "expected"),
        [
            ([U], U),
            ([U, U], U),
            ([U, G], G),
            ([G, D], D),
            ([U, D, G], D),
            ([U, X], X),          # one check could not run: cannot claim Up
            ([X, G], G),          # a definite result outranks Unknown (ASSUMED Q53)
            ([X, D], D),
            ([], X),
        ],
    )
    def test_precedence(self, checks, expected):
        assert worst(checks) is expected


class TestAdvance:
    def _run(self, raws, confirm=2, start=None):
        state = start or NodeState(component="app", node="app1")
        out = []
        for i, raw in enumerate(raws):
            state = advance(state, raw, T0 + i * POLL, confirm)
            out.append(state)
        return out

    def test_initial_state_is_unknown(self):
        assert NodeState(component="c", node="n").status is X

    def test_down_needs_consecutive_failures(self):
        states = self._run([U, D, D])
        assert [s.status for s in states] == [U, U, D]
        assert [s.consecutive_failures for s in states] == [0, 1, 2]

    def test_single_failed_poll_is_ignored(self):
        """The 30 Oct 12-second restart must not show as Down (Q40)."""
        states = self._run([U, D, U, D, U])
        assert all(s.status is U for s in states)

    def test_recovery_is_immediate_and_resets_counter(self):
        states = self._run([U, D, D, U])
        assert states[-1].status is U
        assert states[-1].consecutive_failures == 0

    def test_confirm_after_one_means_no_flap_protection(self):
        assert self._run([U, D], confirm=1)[-1].status is D

    def test_confirm_after_three(self):
        assert [s.status for s in self._run([U, D, D, D], confirm=3)] == [U, U, U, D]

    def test_degraded_is_not_delayed(self):
        assert self._run([U, G])[-1].status is G

    def test_pending_failure_keeps_previous_degraded(self):
        assert [s.status for s in self._run([G, D, D])] == [G, G, D]

    def test_unknown_interrupts_failure_streak(self):
        assert [s.status for s in self._run([U, D, X, D])] == [U, U, X, X]

    def test_first_ever_poll_down_stays_unknown_until_confirmed(self):
        assert [s.status for s in self._run([D, D])] == [X, D]

    def test_since_tracks_last_confirmed_change(self):
        states = self._run([U, U, D, D, D, U])
        assert states[1].since == T0                 # unchanged Up keeps first time
        assert states[2].since == T0                 # pending failure: still Up since T0
        assert states[3].since == T0 + 3 * POLL      # confirmed Down at the 2nd failure
        assert states[4].since == T0 + 3 * POLL
        assert states[5].since == T0 + 5 * POLL

    def test_rejects_naive_and_non_utc_timestamps(self):
        start = NodeState(component="c", node="n")
        with pytest.raises(ValueError, match="UTC"):
            advance(start, U, datetime(2026, 11, 4, 9, 0), 2)
        with pytest.raises(ValueError, match="UTC"):
            advance(start, U, datetime(2026, 11, 4, 9, 0, tzinfo=timezone(timedelta(hours=1))), 2)

    def test_rejects_invalid_confirm_count(self):
        with pytest.raises(ValueError):
            advance(NodeState(component="c", node="n"), D, T0, 0)


def test_check_result_requires_utc():
    CheckResult("app", "app1", "windows_service", U, T0)
    with pytest.raises(ValueError, match="UTC"):
        CheckResult("app", "app1", "windows_service", U, datetime(2026, 11, 4))
