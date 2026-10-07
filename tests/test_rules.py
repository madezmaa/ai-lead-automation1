"""Tests for the deterministic rules engine."""

from __future__ import annotations

from dataclasses import asdict

from app.domain import Decision, LeadProfile
from app.rules import evaluate_rules
from tests.conftest import make_settings

SETTINGS = make_settings()


def profile(**overrides) -> LeadProfile:
    defaults = {
        "email": "alex@acme.io",
        "first_name": "Alex",
        "last_name": "Johnson",
        "company": "Acme Inc",
        "job_title": "VP Sales",
        "phone": "+14155552671",
        "industry": "saas",
        "country": "United States",
        "country_code": "US",
        "source": "referral",
        "message": "Need a demo and pricing.",
        "budget": 20000.0,
        "company_size": 250,
    }
    defaults.update(overrides)
    return LeadProfile(**defaults)


class TestScoring:
    def test_strong_lead_is_qualified(self) -> None:
        result = evaluate_rules(profile(), SETTINGS)
        assert result.decision == Decision.QUALIFIED
        assert result.score == 100

    def test_mid_lead_is_nurture(self) -> None:
        result = evaluate_rules(
            profile(
                first_name=None,
                last_name=None,
                phone=None,
                job_title=None,
                message=None,
                budget=1500.0,
                company_size=50,
                source="inbound",
            ),
            SETTINGS,
        )
        assert result.decision == Decision.NURTURE
        assert 40 <= result.score < 70

    def test_weak_lead_is_disqualified(self) -> None:
        result = evaluate_rules(
            profile(
                email="jane.smith@gmail.com",
                first_name="Jane",
                last_name=None,
                company=None,
                job_title="Support Specialist",
                phone=None,
                industry="retail",
                country=None,
                country_code=None,
                source="website",
                message="Saw your site.",
                budget=None,
                company_size=2,
            ),
            SETTINGS,
        )
        assert result.decision == Decision.DISQUALIFIED
        assert result.score == 0

    def test_score_is_clamped_to_bounds(self) -> None:
        strong = evaluate_rules(profile(), SETTINGS)
        weak = evaluate_rules(
            profile(
                email="jane@gmail.com",
                first_name="Jane",
                last_name=None,
                company=None,
                job_title=None,
                phone=None,
                industry=None,
                country=None,
                country_code=None,
                source="website",
                message=None,
                budget=None,
                company_size=2,
            ),
            SETTINGS,
        )
        assert strong.score == 100
        assert weak.score == 0

    def test_factor_breakdown_is_complete(self) -> None:
        result = evaluate_rules(profile(), SETTINGS)
        names = {factor.name for factor in result.factors}
        assert names == {
            "budget",
            "company_size",
            "email",
            "industry",
            "country",
            "intent",
            "completeness",
            "source",
            "seniority",
        }
        assert all(isinstance(factor.points, int) for factor in result.factors)


class TestHardRules:
    def test_budget_below_floor_disqualifies(self) -> None:
        result = evaluate_rules(profile(budget=500.0), make_settings(min_budget=1000))
        assert result.decision == Decision.DISQUALIFIED
        assert result.hard_disqualified is True
        assert "minimum" in (result.hard_reason or "")

    def test_opt_out_message_disqualifies(self) -> None:
        result = evaluate_rules(profile(message="Please do not contact me again"), SETTINGS)
        assert result.hard_disqualified is True
        assert "opt-out" in (result.hard_reason or "")

    def test_budget_at_floor_is_not_hard(self) -> None:
        result = evaluate_rules(profile(budget=1000.0), SETTINGS)
        assert result.hard_disqualified is False


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        first = evaluate_rules(profile(), SETTINGS)
        second = evaluate_rules(profile(), SETTINGS)
        assert asdict(first) == asdict(second)

    def test_summary_mentions_score_and_decision(self) -> None:
        result = evaluate_rules(profile(), SETTINGS)
        assert "qualified" in result.summary
        assert "100" in result.summary
