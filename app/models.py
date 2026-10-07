"""SQLAlchemy ORM models: ``leads``, ``qualification_results``, ``lead_status_events``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.state_machine import LeadStatus


def utcnow() -> datetime:
    """Timezone-aware current UTC timestamp."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    company: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))
    website: Mapped[str | None] = mapped_column(String(500))
    industry: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    country_code: Mapped[str | None] = mapped_column(String(2))
    source: Mapped[str] = mapped_column(String(60), default="api", index=True, nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    budget: Mapped[float | None] = mapped_column(Numeric(12, 2))
    company_size: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(
        String(32), default=LeadStatus.NEW.value, index=True, nullable=False
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    qualifications: Mapped[list[QualificationResult]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        order_by="QualificationResult.id.desc()",
    )
    events: Mapped[list[LeadStatusEvent]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        order_by="LeadStatusEvent.id.asc()",
    )


class QualificationResult(Base):
    __tablename__ = "qualification_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    rules_output: Mapped[dict] = mapped_column(JSON, nullable=False)
    ai_output: Mapped[dict | None] = mapped_column(JSON)
    ai_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rules_overrode_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    state_from: Mapped[str | None] = mapped_column(String(32))
    state_to: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    lead: Mapped[Lead] = relationship(back_populates="qualifications")


class LeadStatusEvent(Base):
    __tablename__ = "lead_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    lead: Mapped[Lead] = relationship(back_populates="events")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
