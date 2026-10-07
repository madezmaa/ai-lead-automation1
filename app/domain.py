"""Pure domain objects shared by the rules engine, the AI layer and the service."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from app.models import Lead


class Decision(StrEnum):
    """Possible qualification outcomes."""

    QUALIFIED = "qualified"
    NURTURE = "nurture"
    DISQUALIFIED = "disqualified"


def decision_for_score(score: int, qualified_threshold: int, nurture_threshold: int) -> Decision:
    """Map a 0-100 score onto a decision using the configured thresholds."""
    if score >= qualified_threshold:
        return Decision.QUALIFIED
    if score >= nurture_threshold:
        return Decision.NURTURE
    return Decision.DISQUALIFIED


@dataclass(frozen=True, slots=True)
class LeadProfile:
    """A normalized, dependency-free snapshot of a lead used for scoring."""

    email: str
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    job_title: str | None = None
    phone: str | None = None
    website: str | None = None
    industry: str | None = None
    country: str | None = None
    country_code: str | None = None
    source: str = "api"
    message: str | None = None
    budget: float | None = None
    company_size: int | None = None

    @classmethod
    def from_lead(cls, lead: Lead) -> LeadProfile:
        """Build a profile from a persisted lead row."""
        budget = lead.budget
        return cls(
            email=lead.email,
            first_name=lead.first_name,
            last_name=lead.last_name,
            company=lead.company,
            job_title=lead.job_title,
            phone=lead.phone,
            website=lead.website,
            industry=lead.industry,
            country=lead.country,
            country_code=lead.country_code,
            source=lead.source,
            message=lead.message,
            budget=float(budget) if budget is not None else None,
            company_size=lead.company_size,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a plain dict with every field rendered for prompts/logs."""
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class RuleFactor:
    """A single deterministic scoring contribution."""

    name: str
    points: int
    detail: str


@dataclass(frozen=True, slots=True)
class RulesResult:
    """Outcome of the deterministic rules engine."""

    score: int
    decision: Decision
    factors: tuple[RuleFactor, ...]
    hard_disqualified: bool = False
    hard_reason: str | None = None
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = self.decision.value
        return data


@dataclass(frozen=True, slots=True)
class AIResult:
    """Structured output returned by the Ollama qualification layer."""

    score: int
    decision: Decision
    reason: str
    signals: tuple[str, ...]
    model: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = self.decision.value
        return data


@dataclass(frozen=True, slots=True)
class QualificationOutcome:
    """Final decision after merging rules and (optional) AI results."""

    decision: Decision
    score: int
    reason: str
    ai_used: bool
    fallback_used: bool
    rules_overrode_ai: bool


def decide(
    rules_result: RulesResult,
    ai_result: AIResult | None,
    *,
    ai_blend_weight: float,
    qualified_threshold: int,
    nurture_threshold: int,
) -> QualificationOutcome:
    """Combine deterministic rules with the AI layer.

    * No AI result -> rules decide (deterministic fallback).
    * Hard rules (budget floor, opt-out) always beat the AI.
    * Otherwise a weighted blend of both scores decides.
    """
    if ai_result is None:
        return QualificationOutcome(
            decision=rules_result.decision,
            score=rules_result.score,
            reason=rules_result.summary,
            ai_used=False,
            fallback_used=True,
            rules_overrode_ai=False,
        )

    if rules_result.hard_disqualified:
        return QualificationOutcome(
            decision=Decision.DISQUALIFIED,
            score=rules_result.score,
            reason=(
                f"{rules_result.hard_reason}; AI suggested '{ai_result.decision.value}' "
                "but deterministic hard rules take precedence"
            ),
            ai_used=True,
            fallback_used=False,
            rules_overrode_ai=True,
        )

    blended = round(rules_result.score * (1 - ai_blend_weight) + ai_result.score * ai_blend_weight)
    blended = max(0, min(100, blended))
    decision = decision_for_score(blended, qualified_threshold, nurture_threshold)

    if decision == ai_result.decision:
        reason = ai_result.reason
    else:
        reason = (
            f"{ai_result.reason} (AI suggested '{ai_result.decision.value}', "
            f"combined score {blended}: '{decision.value}')"
        )

    return QualificationOutcome(
        decision=decision,
        score=blended,
        reason=reason,
        ai_used=True,
        fallback_used=False,
        rules_overrode_ai=False,
    )


DecisionLiteral = Literal["qualified", "nurture", "disqualified"]
