"""Lead CRUD, qualification, follow-up and status endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from app.ai import OllamaQualifier, render_follow_up_template
from app.config import Settings
from app.db import get_db
from app.domain import LeadProfile
from app.notifications import qualification_payload, send_webhook
from app.schemas import (
    FollowUpDraftRead,
    FollowUpRequest,
    LeadCreate,
    LeadList,
    LeadRead,
    QualificationList,
    QualificationResultRead,
    QualifyRequest,
    StatusUpdateRequest,
)
from app.security import require_api_key
from app.service import (
    change_status,
    create_lead,
    get_lead,
    list_leads,
    list_qualifications,
    qualify_lead,
)
from app.state_machine import LeadStatus

router = APIRouter(prefix="/api/v1/leads", tags=["leads"])

SessionDep = Annotated[Session, Depends(get_db)]
AuthedRequest = Annotated[Request, Depends(require_api_key)]


@router.post("", response_model=LeadRead, status_code=201, summary="Create a lead")
def create_lead_endpoint(
    payload: LeadCreate,
    _: AuthedRequest,
    db: SessionDep,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", max_length=200, description="Repeat-safe create key"),
    ] = None,
) -> LeadRead:
    lead, replayed = create_lead(db, payload, idempotency_key=idempotency_key)
    if replayed:
        response.status_code = 200
    return LeadRead.model_validate(lead)


@router.get("", response_model=LeadList, summary="List leads")
def list_leads_endpoint(
    _: AuthedRequest,
    db: SessionDep,
    status: Annotated[LeadStatus | None, Query(description="Filter by status")] = None,
    source: Annotated[str | None, Query(description="Filter by source")] = None,
    q: Annotated[
        str | None, Query(max_length=200, description="Search by email or company")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LeadList:
    items, total = list_leads(
        db, status=status, source=source, search=q, limit=limit, offset=offset
    )
    return LeadList(
        items=[LeadRead.model_validate(lead) for lead in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{lead_id}", response_model=LeadRead, summary="Get a lead")
def get_lead_endpoint(lead_id: uuid.UUID, _: AuthedRequest, db: SessionDep) -> LeadRead:
    return LeadRead.model_validate(get_lead(db, lead_id))


@router.post(
    "/{lead_id}/qualify",
    response_model=QualificationResultRead,
    summary="Qualify a lead (deterministic rules + optional Ollama AI)",
)
def qualify_lead_endpoint(
    lead_id: uuid.UUID,
    request: Request,
    _: AuthedRequest,
    db: SessionDep,
    body: QualifyRequest | None = None,
) -> QualificationResultRead:
    settings: Settings = request.app.state.settings
    qualifier = OllamaQualifier.from_settings(settings) if settings.ollama_enabled else None
    try:
        result = qualify_lead(
            db,
            lead_id,
            settings=settings,
            qualifier=qualifier,
            use_ai=(body or QualifyRequest()).use_ai,
        )
    finally:
        if qualifier is not None:
            qualifier.close()

    lead = get_lead(db, lead_id)
    if settings.notify_webhook_url:
        send_webhook(
            settings.notify_webhook_url,
            qualification_payload(
                event=f"lead.{result.decision}",
                lead_id=str(lead.id),
                email=lead.email,
                status=result.state_to,
                decision=result.decision,
                score=result.score,
                reason=result.reason,
                ai_used=result.ai_used,
                fallback_used=result.fallback_used,
                source=lead.source,
            ),
            timeout=settings.notify_timeout_seconds,
        )
    return QualificationResultRead.model_validate(result)


@router.post(
    "/{lead_id}/follow-up-draft",
    response_model=FollowUpDraftRead,
    summary="Generate a follow-up email draft (Ollama AI with deterministic fallback)",
)
def follow_up_draft_endpoint(
    lead_id: uuid.UUID,
    request: Request,
    _: AuthedRequest,
    db: SessionDep,
    body: FollowUpRequest | None = None,
) -> FollowUpDraftRead:
    settings: Settings = request.app.state.settings
    lead = get_lead(db, lead_id)
    profile = LeadProfile.from_lead(lead)
    draft_req = body or FollowUpRequest()
    ai_used = False
    draft = render_follow_up_template(profile, tone=draft_req.tone)
    model = None
    if draft_req.use_ai and settings.ollama_enabled:
        qualifier = OllamaQualifier.from_settings(settings)
        try:
            draft, ai_used = qualifier.draft_follow_up(profile, tone=draft_req.tone, use_ai=True)
            model = settings.ollama_model if ai_used else None
        finally:
            qualifier.close()
    return FollowUpDraftRead(
        lead_id=lead.id,
        subject=draft.subject,
        body=draft.body,
        ai_used=ai_used,
        fallback_used=not ai_used,
        model=model,
    )


@router.get(
    "/{lead_id}/qualifications",
    response_model=QualificationList,
    summary="List qualification history for a lead",
)
def qualifications_endpoint(
    lead_id: uuid.UUID,
    _: AuthedRequest,
    db: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QualificationList:
    items, total = list_qualifications(db, lead_id, limit=limit, offset=offset)
    return QualificationList(
        items=[QualificationResultRead.model_validate(item) for item in items],
        total=total,
    )


@router.patch("/{lead_id}/status", response_model=LeadRead, summary="Transition a lead's status")
def change_status_endpoint(
    lead_id: uuid.UUID,
    payload: StatusUpdateRequest,
    _: AuthedRequest,
    db: SessionDep,
) -> LeadRead:
    lead = change_status(db, lead_id, target=payload.status, reason=payload.reason)
    return LeadRead.model_validate(lead)
