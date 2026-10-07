"""Service layer: orchestrates persistence, transitions, rules and AI."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai import AIQualificationError, Qualifier
from app.config import Settings
from app.domain import (
    Decision,
    LeadProfile,
    QualificationOutcome,
    action_for_decision,
    decide,
)
from app.errors import DuplicateLeadError, LeadNotFoundError, NotQualifiedError
from app.models import (
    IdempotencyKey,
    Lead,
    LeadStatusEvent,
    NotificationLog,
    QualificationResult,
    utcnow,
)
from app.rules import evaluate_rules
from app.schemas import LeadCreate
from app.state_machine import LeadStatus, assert_transition

logger = logging.getLogger(__name__)


def _transition(
    session: Session,
    lead: Lead,
    target: LeadStatus,
    *,
    reason: str | None = None,
) -> None:
    current = LeadStatus(lead.status)
    assert_transition(current, target)
    lead.status = target.value
    lead.status_reason = reason
    lead.status_updated_at = utcnow()
    lead.updated_at = utcnow()
    session.add(
        LeadStatusEvent(
            lead_id=lead.id,
            from_status=current.value,
            to_status=target.value,
            reason=reason,
        )
    )


def create_lead(
    session: Session,
    payload: LeadCreate,
    *,
    idempotency_key: str | None = None,
) -> tuple[Lead, bool]:
    """Validate uniqueness, persist a new lead as ``new``, record creation.

    Returns ``(lead, replayed)``. When ``idempotency_key`` is provided and was
    already used, the previously created lead is returned with ``replayed=True``
    and no new row is written.
    """
    if idempotency_key:
        prior = session.get(IdempotencyKey, idempotency_key)
        if prior is not None:
            lead = session.get(Lead, prior.lead_id)
            if lead is not None:
                return lead, True
            session.delete(prior)
            session.flush()

    existing = session.scalar(select(Lead.id).where(Lead.email == payload.email))
    if existing is not None:
        raise DuplicateLeadError(payload.email, existing)

    data = payload.model_dump()
    lead = Lead(**data)
    lead.status = LeadStatus.NEW.value
    lead.status_updated_at = utcnow()
    session.add(lead)
    session.flush()
    session.add(
        LeadStatusEvent(
            lead_id=lead.id,
            from_status=None,
            to_status=LeadStatus.NEW.value,
            reason="Lead created",
        )
    )
    if idempotency_key:
        session.add(IdempotencyKey(key=idempotency_key, lead_id=lead.id))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if idempotency_key:
            prior = session.get(IdempotencyKey, idempotency_key)
            if prior is not None:
                lead = session.get(Lead, prior.lead_id)
                if lead is not None:
                    return lead, True
        conflicting = session.scalar(select(Lead.id).where(Lead.email == payload.email))
        if conflicting is not None:
            raise DuplicateLeadError(payload.email, conflicting) from None
        raise
    session.refresh(lead)
    return lead, False


def get_lead(session: Session, lead_id: object) -> Lead:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise LeadNotFoundError(lead_id)
    return lead


def list_leads(
    session: Session,
    *,
    status: LeadStatus | None = None,
    source: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Lead], int]:
    """Return a page of leads and the total number of matching rows."""
    query = select(Lead)
    count_query = select(func.count(Lead.id))
    if status is not None:
        predicate = Lead.status == status.value
        query = query.where(predicate)
        count_query = count_query.where(predicate)
    if source:
        predicate = Lead.source == source
        query = query.where(predicate)
        count_query = count_query.where(predicate)
    if search:
        pattern = f"%{search.lower()}%"
        predicate = func.lower(Lead.email).like(pattern) | func.lower(Lead.company).like(pattern)
        query = query.where(predicate)
        count_query = count_query.where(predicate)

    total = int(session.scalar(count_query) or 0)
    rows = session.scalars(query.order_by(Lead.created_at.desc()).limit(limit).offset(offset)).all()
    return list(rows), total


def qualify_lead(
    session: Session,
    lead_id: object,
    *,
    settings: Settings,
    qualifier: Qualifier | None,
    use_ai: bool = True,
) -> QualificationResult:
    """Run the full qualification pipeline for a lead.

    Flow: ``<current> -> qualifying -> (qualified|nurture|disqualified)``.
    Any failure inside the AI layer falls back to the deterministic rules.
    Unexpected errors transition the lead to ``failed`` and re-raise.
    """
    lead = get_lead(session, lead_id)
    state_from = LeadStatus(lead.status)

    _transition(session, lead, LeadStatus.QUALIFYING, reason="Qualification started")
    session.commit()

    def _decide(rules, ai) -> QualificationOutcome:
        return decide(
            rules,
            ai,
            ai_blend_weight=settings.ai_blend_weight,
            qualified_threshold=settings.qualified_threshold,
            nurture_threshold=settings.nurture_threshold,
        )

    try:
        profile = LeadProfile.from_lead(lead)
        rules_result = evaluate_rules(profile, settings)
        ai_result = None
        if use_ai and settings.ollama_enabled and qualifier is not None:
            ai_result = qualifier.qualify(profile)
        outcome = _decide(rules_result, ai_result)
    except AIQualificationError:
        logger.warning("AI qualification failed; falling back to rules for lead %s", lead_id)
        outcome = _decide(rules_result, None)
    except Exception:
        logger.exception("Unexpected error during qualification of lead %s", lead_id)
        try:
            lead = get_lead(session, lead_id)
            _transition(session, lead, LeadStatus.FAILED, reason="Qualification crashed")
            session.commit()
        except Exception:
            session.rollback()
        raise

    state_to = _outcome_status(outcome)
    _transition(session, lead, state_to, reason=outcome.reason)

    result = QualificationResult(
        lead_id=lead.id,
        decision=outcome.decision.value,
        score=outcome.score,
        recommended_action=action_for_decision(outcome.decision),
        reason=outcome.reason,
        rules_output=rules_result.to_dict(),
        ai_output=ai_result.to_dict() if ai_result is not None else None,
        ai_used=outcome.ai_used,
        fallback_used=outcome.fallback_used,
        rules_overrode_ai=outcome.rules_overrode_ai,
        model=ai_result.model if ai_result is not None else None,
        state_from=state_from.value,
        state_to=state_to.value,
    )
    session.add(result)
    session.commit()
    session.refresh(result)
    return result


def _outcome_status(outcome: QualificationOutcome) -> LeadStatus:
    if outcome.decision == Decision.QUALIFIED:
        return LeadStatus.QUALIFIED
    if outcome.decision == Decision.NURTURE:
        return LeadStatus.NURTURE
    return LeadStatus.DISQUALIFIED


def change_status(
    session: Session,
    lead_id: object,
    *,
    target: LeadStatus,
    reason: str | None = None,
) -> Lead:
    """Manually move a lead through the state machine (e.g. from n8n)."""
    lead = get_lead(session, lead_id)
    _transition(session, lead, target, reason=reason or "Manual status change")
    session.commit()
    session.refresh(lead)
    return lead


def list_qualifications(
    session: Session, lead_id: object, *, limit: int = 20, offset: int = 0
) -> tuple[Sequence[QualificationResult], int]:
    lead = get_lead(session, lead_id)
    query = select(QualificationResult).where(QualificationResult.lead_id == lead.id)
    total = int(
        session.scalar(
            select(func.count(QualificationResult.id)).where(QualificationResult.lead_id == lead.id)
        )
        or 0
    )
    rows = session.scalars(
        query.order_by(QualificationResult.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return rows, total


def latest_qualification(session: Session, lead_id: object) -> tuple[Lead, QualificationResult]:
    """Return the lead with its most recent qualification result.

    Raises :class:`NotQualifiedError` when the lead has never been qualified.
    """
    lead = get_lead(session, lead_id)
    result = session.scalar(
        select(QualificationResult)
        .where(QualificationResult.lead_id == lead.id)
        .order_by(QualificationResult.id.desc())
        .limit(1)
    )
    if result is None:
        raise NotQualifiedError(lead_id)
    return lead, result


def list_notifications(
    session: Session, lead_id: object, *, limit: int = 20, offset: int = 0
) -> tuple[Sequence[NotificationLog], int]:
    """Return the notification delivery log for a lead (newest first)."""
    lead = get_lead(session, lead_id)
    total = int(
        session.scalar(
            select(func.count(NotificationLog.id)).where(NotificationLog.lead_id == lead.id)
        )
        or 0
    )
    rows = session.scalars(
        select(NotificationLog)
        .where(NotificationLog.lead_id == lead.id)
        .order_by(NotificationLog.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return rows, total
