"""Tests for the Ollama AI layer, structured output parsing and the combiner."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from app.ai import AIQualificationError, AIQualificationOutput, OllamaQualifier
from app.domain import AIResult, Decision, LeadProfile, RuleFactor, RulesResult, decide


def _qualifier(handler, **kwargs) -> OllamaQualifier:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaQualifier(
        base_url="http://ollama",
        model="test-model",
        timeout=5,
        client=client,
        **kwargs,
    )


def _ok_handler(payload: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body.get("format") == "json"
        assert body["options"]["temperature"] == 0.0
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": json.dumps(payload)}}
        )

    return handler


def _profile() -> LeadProfile:
    return LeadProfile(email="alex@acme.io", company="Acme", budget=20000.0, company_size=250)


class TestStructuredOutput:
    def test_valid_json_is_parsed(self) -> None:
        qualifier = _qualifier(
            _ok_handler(
                {
                    "score": 88,
                    "decision": "qualified",
                    "reason": "Strong enterprise fit.",
                    "signals": ["enterprise budget", "senior title"],
                }
            )
        )
        result = qualifier.qualify(_profile())
        assert isinstance(result, AIResult)
        assert result.decision == Decision.QUALIFIED
        assert result.score == 88
        assert result.reason == "Strong enterprise fit."
        assert result.signals == ("enterprise budget", "senior title")
        assert result.model == "test-model"

    def test_score_is_clamped_and_coerced(self) -> None:
        qualifier = _qualifier(
            _ok_handler(
                {
                    "score": "140",
                    "decision": "qualified",
                    "reason": "high",
                    "signals": [],
                }
            )
        )
        assert qualifier.qualify(_profile()).score == 100

    def test_markdown_fences_are_stripped(self) -> None:
        fenced = '```json\n{"score": 40, "decision": "nurture", "reason": "ok", "signals": []}\n```'

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"role": "assistant", "content": fenced}})

        result = _qualifier(handler).qualify(_profile())
        assert result.decision == Decision.NURTURE
        assert result.score == 40

    @pytest.mark.parametrize(
        "payload",
        [
            '{"score": 90, "reason": "x"}',  # missing decision
            '{"score": 90, "decision": "maybe", "reason": "x"}',  # bad enum
            "not json at all",  # invalid JSON
            '{"score": "abc", "decision": "qualified", "reason": "x"}',  # bad score
        ],
    )
    def test_schema_violations_raise(self, payload) -> None:
        qualifier = _qualifier(_ok_handler(payload))
        with pytest.raises(AIQualificationError):
            qualifier.qualify(_profile())


class TestFailures:
    def test_missing_content(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        qualifier = _qualifier(handler, max_retries=1)
        with pytest.raises(AIQualificationError, match="no message content"):
            qualifier.qualify(_profile())

    def test_http_error_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="model loading")

        qualifier = _qualifier(handler, max_retries=1)
        with pytest.raises(AIQualificationError, match="503"):
            qualifier.qualify(_profile())

    def test_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        qualifier = _qualifier(handler, max_retries=1)
        with pytest.raises(AIQualificationError, match="timed out"):
            qualifier.qualify(_profile())


VALID_PAYLOAD = {
    "score": 75,
    "decision": "qualified",
    "reason": "solid fit",
    "signals": [],
}


class TestRetries:
    def test_transient_500_is_retried_then_succeeds(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, text="flaky")
            return httpx.Response(200, json={"message": {"content": json.dumps(VALID_PAYLOAD)}})

        result = _qualifier(handler, max_retries=2).qualify(_profile())
        assert calls["n"] == 2
        assert result.score == 75

    def test_timeout_is_retried_then_succeeds(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("slow")
            return httpx.Response(200, json={"message": {"content": json.dumps(VALID_PAYLOAD)}})

        _qualifier(handler, max_retries=3).qualify(_profile())
        assert calls["n"] == 2

    def test_gives_up_after_max_retries(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, text="down")

        with pytest.raises(AIQualificationError, match="500"):
            _qualifier(handler, max_retries=3).qualify(_profile())
        assert calls["n"] == 3

    def test_schema_error_is_not_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"message": {"content": '{"score": 10}'}})

        with pytest.raises(AIQualificationError):
            _qualifier(handler, max_retries=3).qualify(_profile())
        assert calls["n"] == 1


class TestOutputSchema:
    def test_clamp_and_coerce(self) -> None:
        assert (
            AIQualificationOutput.model_validate(
                {"score": "-5", "decision": "nurture", "reason": "r"}
            ).score
            == 0
        )
        assert (
            AIQualificationOutput.model_validate(
                {"score": 7.6, "decision": "nurture", "reason": "r"}
            ).score
            == 8
        )

    def test_bool_score_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AIQualificationOutput.model_validate(
                {"score": True, "decision": "nurture", "reason": "r"}
            )

    def test_blank_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AIQualificationOutput.model_validate(
                {"score": 10, "decision": "nurture", "reason": "   "}
            )


def _rules(score: int, *, hard: str | None = None) -> RulesResult:
    return RulesResult(
        score=score,
        decision=Decision.NURTURE if 40 <= score < 70 else Decision.DISQUALIFIED,
        factors=(RuleFactor("budget", 10, "x"),),
        hard_disqualified=hard is not None,
        hard_reason=hard,
        summary=f"rules score {score}/100",
    )


def _ai(score: int, decision: Decision = Decision.QUALIFIED) -> AIResult:
    return AIResult(
        score=score, decision=decision, reason="AI says fit", signals=("demo",), model="m"
    )


class TestDecide:
    def test_no_ai_falls_back_to_rules(self) -> None:
        outcome = decide(
            _rules(60), None, ai_blend_weight=0.6, qualified_threshold=70, nurture_threshold=40
        )
        assert outcome.decision == Decision.NURTURE
        assert outcome.score == 60
        assert outcome.fallback_used is True
        assert outcome.ai_used is False

    def test_hard_rules_override_ai(self) -> None:
        outcome = decide(
            _rules(90, hard="budget below floor"),
            _ai(95),
            ai_blend_weight=0.6,
            qualified_threshold=70,
            nurture_threshold=40,
        )
        assert outcome.decision == Decision.DISQUALIFIED
        assert outcome.rules_overrode_ai is True
        assert outcome.ai_used is True
        assert "budget below floor" in outcome.reason

    def test_blend_math(self) -> None:
        outcome = decide(
            _rules(60),
            _ai(80),
            ai_blend_weight=0.6,
            qualified_threshold=70,
            nurture_threshold=40,
        )
        assert outcome.score == 72  # round(60*0.4 + 80*0.6)
        assert outcome.decision == Decision.QUALIFIED

    def test_score_clamped_to_100(self) -> None:
        outcome = decide(
            _rules(100),
            _ai(100),
            ai_blend_weight=0.6,
            qualified_threshold=70,
            nurture_threshold=40,
        )
        assert outcome.score == 100
