"""Tests for the lead state machine."""

from __future__ import annotations

import pytest

from app.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    LeadStatus,
    assert_transition,
    can_transition,
)

TERMINAL_OUTCOMES = {
    LeadStatus.QUALIFIED,
    LeadStatus.NURTURE,
    LeadStatus.DISQUALIFIED,
    LeadStatus.FAILED,
}


class TestTransitions:
    def test_new_can_qualify(self) -> None:
        assert can_transition(LeadStatus.NEW, LeadStatus.QUALIFYING)

    def test_qualifying_resolves_to_all_outcomes(self) -> None:
        assert can_transition(LeadStatus.QUALIFYING, LeadStatus.QUALIFIED)
        assert can_transition(LeadStatus.QUALIFYING, LeadStatus.NURTURE)
        assert can_transition(LeadStatus.QUALIFYING, LeadStatus.DISQUALIFIED)
        assert can_transition(LeadStatus.QUALIFYING, LeadStatus.FAILED)

    def test_skipping_states_is_blocked(self) -> None:
        assert not can_transition(LeadStatus.NEW, LeadStatus.QUALIFIED)
        assert not can_transition(LeadStatus.NEW, LeadStatus.NURTURE)
        assert not can_transition(LeadStatus.NEW, LeadStatus.DISQUALIFIED)

    @pytest.mark.parametrize("terminal", sorted(TERMINAL_OUTCOMES))
    def test_terminal_states_can_re_qualify(self, terminal: LeadStatus) -> None:
        assert can_transition(terminal, LeadStatus.QUALIFYING)

    def test_archived_is_almost_terminal(self) -> None:
        assert not can_transition(LeadStatus.ARCHIVED, LeadStatus.QUALIFYING)
        assert can_transition(LeadStatus.ARCHIVED, LeadStatus.NEW)

    def test_assert_raises_with_guidance(self) -> None:
        with pytest.raises(InvalidTransitionError) as exc_info:
            assert_transition(LeadStatus.NEW, LeadStatus.QUALIFIED)
        message = str(exc_info.value)
        assert "new" in message
        assert "qualified" in message
        assert "qualifying" in message

    def test_every_status_has_an_entry(self) -> None:
        for status in LeadStatus:
            assert status in ALLOWED_TRANSITIONS
            assert all(isinstance(t, LeadStatus) for t in ALLOWED_TRANSITIONS[status])

    def test_no_self_transitions(self) -> None:
        for status, targets in ALLOWED_TRANSITIONS.items():
            assert status not in targets
