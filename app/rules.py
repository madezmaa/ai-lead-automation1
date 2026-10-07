"""Deterministic lead qualification rules.

Pure functions with zero I/O: given the same profile and settings they always
return the same result. Used both as the primary qualifier and as the
fallback whenever the AI layer is unavailable.
"""

from __future__ import annotations

from app.config import Settings
from app.domain import Decision, LeadProfile, RuleFactor, RulesResult, decision_for_score
from app.normalization import is_free_email

_INTENT_KEYWORDS = (
    "pricing",
    "price",
    "quote",
    "demo",
    "trial",
    "buy",
    "purchase",
    "contract",
    "implement",
    "integrat",
    "launch",
    "asap",
    "budget",
    "procurement",
    "rfp",
    "start immediately",
)
_OPT_OUT_KEYWORDS = (
    "unsubscribe",
    "remove me",
    "do not contact",
    "stop emailing",
    "not interested",
    "take me off",
)
_SENIOR_TITLE_WORDS = (
    "founder",
    "co-founder",
    "ceo",
    "cto",
    "cmo",
    "cio",
    "vp",
    "vice president",
    "head",
    "director",
    "owner",
    "principal",
    "managing partner",
)
_REFERRAL_SOURCES = ("referral", "partner", "affiliate", "agency-partner")

_MAX_SCORE = 100
_MIN_SCORE = 0


def _contains_any(text: str | None, keywords: tuple[str, ...]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


def _budget_points(budget: float | None, min_budget: float) -> tuple[int, str]:
    if budget is None:
        return 0, "budget not disclosed"
    if budget >= 25_000:
        return 30, f"budget >= $25k (${budget:,.0f})"
    if budget >= 10_000:
        return 25, f"budget >= $10k (${budget:,.0f})"
    if budget >= 5_000:
        return 18, f"budget >= $5k (${budget:,.0f})"
    if budget >= min_budget:
        return 10, f"budget >= ${min_budget:,.0f} (${budget:,.0f})"
    return -10, f"budget below floor (${budget:,.0f})"


def _company_size_points(size: int | None) -> tuple[int, str]:
    if size is None:
        return 0, "company size not disclosed"
    if size >= 500:
        return 25, f"{size} employees (enterprise)"
    if size >= 200:
        return 20, f"{size} employees (upper mid-market)"
    if size >= 50:
        return 14, f"{size} employees (mid-market)"
    if size >= 10:
        return 8, f"{size} employees (SMB)"
    return 2, f"{size} employees (micro)"


def evaluate_rules(profile: LeadProfile, settings: Settings) -> RulesResult:
    """Score a lead with deterministic rules and return the detail breakdown."""
    factors: list[RuleFactor] = []
    hard_reason: str | None = None
    message = profile.message or ""

    if profile.budget is not None and profile.budget < settings.min_budget:
        hard_reason = (
            f"declared budget (${profile.budget:,.0f}) is below the "
            f"${settings.min_budget:,.0f} minimum"
        )
    elif _contains_any(message, _OPT_OUT_KEYWORDS):
        hard_reason = "message contains an opt-out / 'do not contact' signal"

    factors.append(_factor("budget", *_budget_points(profile.budget, settings.min_budget)))
    factors.append(_factor("company_size", *_company_size_points(profile.company_size)))

    domain = profile.email.rsplit("@", 1)[-1].lower()
    if is_free_email(profile.email, settings.free_email_domains):
        factors.append(RuleFactor("email", -5, f"free consumer mailbox ({domain})"))
    else:
        factors.append(RuleFactor("email", 10, f"business mailbox ({domain})"))

    if profile.industry:
        industry = profile.industry.lower()
        if any(target in industry for target in settings.target_industries):
            factors.append(RuleFactor("industry", 15, f"target industry: {profile.industry}"))
        else:
            factors.append(RuleFactor("industry", 0, f"non-target industry: {profile.industry}"))
    else:
        factors.append(RuleFactor("industry", 0, "industry not disclosed"))

    if profile.country_code and profile.country_code in settings.target_countries:
        factors.append(
            RuleFactor("country", 10, f"target market: {profile.country or profile.country_code}")
        )
    elif profile.country or profile.country_code:
        market = profile.country or profile.country_code
        factors.append(RuleFactor("country", -5, f"non-target market: {market}"))
    else:
        factors.append(RuleFactor("country", 0, "country not disclosed"))

    if _contains_any(message, _INTENT_KEYWORDS):
        factors.append(RuleFactor("intent", 15, "buying-intent keywords in message"))
    else:
        factors.append(RuleFactor("intent", 0, "no buying-intent keywords in message"))

    completeness = sum(
        1
        for field in (profile.first_name, profile.last_name, profile.company, profile.phone)
        if field
    )
    if completeness >= 3:
        factors.append(RuleFactor("completeness", 5, f"{completeness}/4 contact fields complete"))
    else:
        factors.append(RuleFactor("completeness", 0, f"{completeness}/4 contact fields complete"))

    source = (profile.source or "").lower()
    if source in _REFERRAL_SOURCES or any(s in source for s in _REFERRAL_SOURCES):
        factors.append(RuleFactor("source", 8, f"referral channel: {source}"))
    else:
        factors.append(RuleFactor("source", 0, f"source: {source or 'n/a'}"))

    if _contains_any(profile.job_title, _SENIOR_TITLE_WORDS):
        factors.append(RuleFactor("seniority", 8, f"senior title: {profile.job_title}"))
    elif profile.job_title:
        factors.append(RuleFactor("seniority", 0, f"title: {profile.job_title}"))
    else:
        factors.append(RuleFactor("seniority", 0, "title not disclosed"))

    score = sum(factor.points for factor in factors)
    score = max(_MIN_SCORE, min(_MAX_SCORE, score))
    decision = decision_for_score(score, settings.qualified_threshold, settings.nurture_threshold)

    if hard_reason is not None:
        decision = Decision.DISQUALIFIED

    top_factors = ", ".join(f"{f.name}({f.points:+d})" for f in factors[:3])
    summary = f"rules score {score}/100 -> {decision.value} | factors: {top_factors}"

    return RulesResult(
        score=score,
        decision=decision,
        factors=tuple(factors),
        hard_disqualified=hard_reason is not None,
        hard_reason=hard_reason,
        summary=summary,
    )


def _factor(name: str, points: int, detail: str) -> RuleFactor:
    return RuleFactor(name=name, points=points, detail=detail)
