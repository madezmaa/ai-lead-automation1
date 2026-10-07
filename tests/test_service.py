"""Service-layer tests: persistence, state transitions, AI fallback."""

from __future__ import annotations

import uuid

import pytest

from app.ai import AIQualificationError, Qualifier
from app.domain import AIResult, Decision, LeadProfile
from app.errors import DuplicateLeadError, LeadNotFoundError
from app.schemas import LeadCreate
from app.service import change_status, create_lead, get_lead, qualify_lead
from app.state_machine import InvalidTransitionError, LeadStatus
from tests.conftest import make_settings

SETTINGS = make_settings()
AI_SETTINGS = make_settings(ollama_enabled=True)


class FakeQualifier(Qualifier):
    def __init__(self, result: AIResult | Exception) -> None:
        self.result = result

    def qualify(self, profile: LeadProfile) -> AIResult:
        if isinstance(self.result, AIResult):
            return self.result
        raise self.result


def _create(db, payload: dict):
    lead, _replayed = create_lead(db, LeadCreate.model_validate(payload))
    return lead


class TestCreateLead:
    def test_creates_as_new_with_event(self, db_session, strong_payload: dict) -> None:
        lead = _create(db_session, strong_payload)
        assert lead.status == LeadStatus.NEW.value
        assert lead.email == "alex.johnson@acme.io"
        events = list(lead.events)
        assert len(events) == 1
        assert events[0].from_status is None
        assert events[0].to_status == "new"

    def test_duplicate_email_rejected(self, db_session, strong_payload: dict) -> None:
        _create(db_session, strong_payload)
        with pytest.raises(DuplicateLeadError):
            _create(db_session, strong_payload | {"first_name": "Other"})

    def test_get_unknown_raises(self, db_session) -> None:
        with pytest.raises(LeadNotFoundError):
            get_lead(db_session, uuid.UUID("00000000-0000-0000-0000-000000000000"))


class TestQualifyLead:
    def test_qualified_with_ai(self, db_session, strong_payload: dict) -> None:
        lead = _create(db_session, strong_payload)
        ai = AIResult(
            score=85, decision=Decision.QUALIFIED, reason="AI loves it", signals=(), model="m"
        )
        result = qualify_lead(
            db_session, lead.id, settings=AI_SETTINGS, qualifier=FakeQualifier(ai), use_ai=True
        )
        assert result.ai_used is True
        assert result.fallback_used is False
        assert result.decision == Decision.QUALIFIED.value
        assert result.state_from == "new"
        assert result.state_to == "qualified"
        refreshed = get_lead(db_session, lead.id)
        assert refreshed.status == LeadStatus.QUALIFIED.value
        events = [e.to_status for e in refreshed.events]
        assert events == ["new", "qualifying", "qualified"]

    def test_falls_back_to_rules_when_ai_fails(self, db_session, strong_payload: dict) -> None:
        lead = _create(db_session, strong_payload)
        failing = FakeQualifier(AIQualificationError("model on fire"))
        result = qualify_lead(
            db_session, lead.id, settings=AI_SETTINGS, qualifier=failing, use_ai=True
        )
        # Strong lead: rules alone still qualify it.
        assert result.ai_used is False
        assert result.fallback_used is True
        assert result.decision == Decision.QUALIFIED.value
        assert result.ai_output is None
        assert result.model is None
        assert get_lead(db_session, lead.id).status == LeadStatus.QUALIFIED.value

    def test_falls_back_to_rules_when_ai_disabled(self, db_session, strong_payload: dict) -> None:
        lead = _create(db_session, strong_payload)
        result = qualify_lead(db_session, lead.id, settings=SETTINGS, qualifier=None, use_ai=True)
        assert result.ai_used is False
        assert result.fallback_used is True

    def test_hard_rules_override_ai(self, db_session) -> None:
        payload = LeadCreate.model_validate(
            {
                "email": "alex@acme.io",
                "budget": 500,
                "message": "Please do not contact me again",
            }
        )
        lead, _ = create_lead(db_session, payload)
        ai = AIResult(
            score=95, decision=Decision.QUALIFIED, reason="AI says yes", signals=(), model="m"
        )
        result = qualify_lead(
            db_session, lead.id, settings=AI_SETTINGS, qualifier=FakeQualifier(ai)
        )
        assert result.decision == Decision.DISQUALIFIED.value
        assert result.rules_overrode_ai is True
        assert result.ai_used is True
        assert get_lead(db_session, lead.id).status == LeadStatus.DISQUALIFIED.value

    def test_archived_lead_cannot_qualify(self, db_session, weak_payload: dict) -> None:
        lead = _create(db_session, weak_payload)
        change_status(db_session, lead.id, target=LeadStatus.ARCHIVED, reason="gone")
        with pytest.raises(InvalidTransitionError):
            qualify_lead(db_session, lead.id, settings=SETTINGS, qualifier=None, use_ai=True)

    def test_crash_marks_lead_failed_and_reraises(self, db_session, strong_payload: dict) -> None:
        lead = _create(db_session, strong_payload)
        with pytest.raises(RuntimeError, match="boom"):
            qualify_lead(
                db_session,
                lead.id,
                settings=AI_SETTINGS,
                qualifier=FakeQualifier(RuntimeError("boom")),
                use_ai=True,
            )
        assert get_lead(db_session, lead.id).status == LeadStatus.FAILED.value

    def test_re_qualification_appends_history(self, db_session, strong_payload: dict) -> None:
        lead = _create(db_session, strong_payload)
        ai = AIResult(score=85, decision=Decision.QUALIFIED, reason="fit", signals=(), model="m")
        first = qualify_lead(db_session, lead.id, settings=AI_SETTINGS, qualifier=FakeQualifier(ai))
        second = qualify_lead(
            db_session, lead.id, settings=AI_SETTINGS, qualifier=FakeQualifier(ai)
        )
        assert first.state_from == "new"
        assert second.state_from == "qualified"
        assert len(lead.qualifications) == 2


class TestManualTransition:
    def test_valid_transition(self, db_session, strong_payload: dict) -> None:
        lead = _create(db_session, strong_payload)
        changed = change_status(db_session, lead.id, target=LeadStatus.ARCHIVED, reason="ma")
        assert changed.status == LeadStatus.ARCHIVED.value

    def test_invalid_transition(self, db_session, strong_payload: dict) -> None:
        lead = _create(db_session, strong_payload)
        with pytest.raises(InvalidTransitionError):
            change_status(db_session, lead.id, target=LeadStatus.QUALIFIED)
