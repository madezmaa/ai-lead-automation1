"""Pydantic request/response schemas for the API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.normalization import (
    ISO2_TO_NAME,
    clean_text,
    normalize_country,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_source,
    normalize_website,
)
from app.state_machine import LeadStatus


class LeadCreate(BaseModel):
    """Incoming lead payload. All fields are normalized before persistence."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=320, description="Required, used as dedup key")
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    company: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    website: str | None = Field(default=None, max_length=500)
    industry: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    country_code: str | None = Field(default=None, max_length=2)
    source: str = Field(default="api", max_length=60)
    message: str | None = Field(default=None, max_length=5000)
    budget: float | None = Field(default=None, ge=0, le=1_000_000_000)
    company_size: int | None = Field(default=None, ge=0, le=10_000_000)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("first_name", "last_name")
    @classmethod
    def _person_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_name(value)

    @field_validator("company", "industry", "job_title")
    @classmethod
    def _plain_text(cls, value: str | None) -> str | None:
        return clean_text(value)

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_phone(value)

    @field_validator("website")
    @classmethod
    def _website(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_website(value)

    @field_validator("source")
    @classmethod
    def _source(cls, value: str | None) -> str:
        return normalize_source(value)

    @model_validator(mode="after")
    def _country(self) -> LeadCreate:
        name, code = normalize_country(self.country)
        if name is not None:
            self.country = name
        if code is not None:
            self.country_code = code
        elif self.country is None and self.country_code in ISO2_TO_NAME:
            self.country_code = self.country_code.upper()
        else:
            self.country_code = None
        return self


class LeadRead(BaseModel):
    """Lead as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    first_name: str | None
    last_name: str | None
    company: str | None
    job_title: str | None
    phone: str | None
    website: str | None
    industry: str | None
    country: str | None
    country_code: str | None
    source: str
    message: str | None
    budget: float | None
    company_size: int | None
    status: str
    status_reason: str | None
    status_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LeadList(BaseModel):
    items: list[LeadRead]
    total: int
    limit: int
    offset: int


class QualifyRequest(BaseModel):
    use_ai: bool = True


class QualificationResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: uuid.UUID
    decision: str
    score: int
    reason: str
    rules_output: dict
    ai_output: dict | None
    ai_used: bool
    fallback_used: bool
    rules_overrode_ai: bool
    model: str | None
    state_from: str | None
    state_to: str
    created_at: datetime


class QualificationList(BaseModel):
    items: list[QualificationResultRead]
    total: int


class StatusUpdateRequest(BaseModel):
    status: LeadStatus
    reason: str | None = Field(default=None, max_length=1000)


class FollowUpRequest(BaseModel):
    use_ai: bool = True
    tone: Literal["professional", "friendly"] = "professional"


class FollowUpDraftRead(BaseModel):
    lead_id: uuid.UUID
    subject: str
    body: str
    ai_used: bool
    fallback_used: bool
    model: str | None = None


class HealthResponse(BaseModel):
    status: str
    database: str
    ollama: dict


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
