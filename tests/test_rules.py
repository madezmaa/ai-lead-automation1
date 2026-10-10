"""Tests for the deterministic rules engine."""

from __future__ import annotations

from dataclasses import asdict

import pytest

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
            "timeline",
            "completeness",
            "source",
            "seniority",
        }
        assert all(isinstance(factor.points, int) for factor in result.factors)


class TestEmailDomains:
    """Reserved/example domains must never earn business-email credit."""

    RESERVED = (
        "john@apexgrowth.example",
        "david@example.com",
        "a@example.org",
        "b@example.net",
        "c@host.example",
        "d@something.test",
        "e@blocked.invalid",
        "f@dev.localhost",
        "g@box.local",
    )

    def test_reserved_domain_gets_zero_email_points(self) -> None:
        for address in self.RESERVED:
            result = evaluate_rules(profile(email=address), SETTINGS)
            email_factor = next(f for f in result.factors if f.name == "email")
            assert email_factor.points == 0, address
            assert "reserved" in email_factor.detail.lower(), address
            assert "business" not in email_factor.detail.lower(), address
            assert "verified" not in email_factor.detail.lower(), address

    def test_business_domain_is_not_described_as_verified(self) -> None:
        result = evaluate_rules(profile(email="alex@acme.io"), SETTINGS)
        email_factor = next(f for f in result.factors if f.name == "email")
        assert email_factor.points == 10
        assert "verified" not in email_factor.detail.lower()

    def test_free_domain_is_penalised(self) -> None:
        result = evaluate_rules(profile(email="x@gmail.com"), SETTINGS)
        email_factor = next(f for f in result.factors if f.name == "email")
        assert email_factor.points == -5


class TestTimeline:
    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Please call ASAP", 15),
            ("We need this immediately", 15),
            ("Urgent requirement", 15),
            ("Can we start this week?", 15),
            ("Looking to implement within 2 weeks", 10),
            ("Targeting next month", 10),
            ("Planning for this quarter", 10),
            ("Someday, no rush", 0),
            (None, 0),
        ],
    )
    def test_timeline_scoring(self, message: str | None, expected: int) -> None:
        result = evaluate_rules(profile(message=message), SETTINGS)
        factor = next(f for f in result.factors if f.name == "timeline")
        assert factor.points == expected


class TestBudgetBoundaries:
    @pytest.mark.parametrize(
        "budget,expected",
        [
            (None, 0),
            (999.0, -10),
            (1000.0, 10),
            (5000.0, 18),
            (10000.0, 25),
            (25000.0, 30),
        ],
    )
    def test_budget_tiers(self, budget: float | None, expected: int) -> None:
        result = evaluate_rules(profile(budget=budget), make_settings(min_budget=1000))
        factor = next(f for f in result.factors if f.name == "budget")
        assert factor.points == expected


class TestDemoPresets:
    """The demo's labelled presets must land in their documented bands.

    These mirror the exact payloads ``demo/app.js`` submits for each sample, so a
    regression that mislabels a sample fails here.
    """

    def _demo(self, **fields) -> tuple[int, Decision]:
        base = {
            "email": "x@apexgrowth.example",
            "first_name": "Pat",
            "last_name": "Lee",
            "company": "Sample Co",
            "source": "website",
            "budget": None,
            "company_size": None,
        }
        base.update(fields)
        result = evaluate_rules(LeadProfile(**base), SETTINGS)
        return result.score, result.decision

    def test_hot_preset_is_qualified(self) -> None:
        score, decision = self._demo(
            email="john@apexgrowth.example",
            first_name="John",
            last_name="Smith",
            company="Apex Growth",
            job_title="VP of Sales",
            industry="saas",
            country="United States",
            country_code="US",
            company_size=50,
            budget=5000.0,
            message=(
                "We need an automated system to qualify our inbound leads and route "
                "qualified prospects to our sales team. We are ready to purchase and "
                "would like a demo and pricing. Budget: $3,000-$5,000. "
                "Timeline: Within 2 weeks"
            ),
        )
        assert decision == Decision.QUALIFIED, score

    def test_warm_preset_is_nurture(self) -> None:
        score, decision = self._demo(
            email="sarah@northstar.example",
            first_name="Sarah",
            last_name="Miller",
            company="Northstar Media",
            industry="media",
            country="Canada",
            country_code="CA",
            company_size=20,
            budget=2000.0,
            message=(
                "We are interested in automating lead qualification and CRM updates, "
                "but we are still evaluating options. Budget: $1,000-$2,000. "
                "Timeline: 1-2 months"
            ),
        )
        assert decision == Decision.NURTURE, score

    def test_cold_preset_is_disqualified(self) -> None:
        score, decision = self._demo(
            email="david@example.com",
            first_name="David",
            last_name="Brown",
            company="Small Business",
            industry="retail",
            country="Other",
            company_size=2,
            message="Looking for information about automation.",
        )
        assert decision == Decision.DISQUALIFIED, score


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
