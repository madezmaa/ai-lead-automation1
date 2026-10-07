"""Tests for follow-up draft generation (template + Ollama AI)."""

from __future__ import annotations

import json

import httpx

from app.ai import OllamaQualifier, render_follow_up_template
from app.domain import LeadProfile


def _profile() -> LeadProfile:
    return LeadProfile(
        email="alex@acme.io",
        first_name="Alex",
        company="Acme Inc",
        job_title="VP Sales",
        message="Need pricing for 50 seats.",
    )


def _qualifier(handler) -> OllamaQualifier:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaQualifier(base_url="http://ollama", model="test-model", timeout=5, client=client)


class TestTemplate:
    def test_contains_lead_context(self) -> None:
        draft = render_follow_up_template(_profile())
        assert "Alex" in draft.body
        assert "Acme Inc" in draft.body
        assert "Acme Inc" in draft.subject
        assert draft.body  # non-empty deterministic content

    def test_tone_changes_subject(self) -> None:
        prof = _profile()
        assert (
            render_follow_up_template(prof, tone="friendly").subject
            != render_follow_up_template(prof, tone="professional").subject
        )

    def test_missing_names_use_safe_defaults(self) -> None:
        draft = render_follow_up_template(LeadProfile(email="x@y.io"))
        assert "there" in draft.body


class TestAiDraft:
    def test_ai_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["format"] == "json"
            content = json.dumps({"subject": "Re: demo", "body": "Hi, calling you tomorrow."})
            return httpx.Response(200, json={"message": {"content": content}})

        draft, ai_used = _qualifier(handler).draft_follow_up(_profile(), use_ai=True)
        assert ai_used is True
        assert draft.subject == "Re: demo"
        assert draft.body == "Hi, calling you tomorrow."

    def test_ai_failure_falls_back_to_template(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        draft, ai_used = _qualifier(handler).draft_follow_up(
            _profile(), use_ai=True, tone="friendly"
        )
        assert ai_used is False
        assert "Alex" in draft.body

    def test_invalid_json_falls_back(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"content": "not json"}})

        draft, ai_used = _qualifier(handler).draft_follow_up(_profile(), use_ai=True)
        assert ai_used is False
        assert draft.subject

    def test_use_ai_false_never_calls_transport(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("transport must not be used")

        draft, ai_used = _qualifier(handler).draft_follow_up(_profile(), use_ai=False)
        assert ai_used is False
        assert "Acme Inc" in draft.body


class TestApiEndpoint:
    def test_follow_up_endpoint_with_ai_disabled(self, client, strong_payload) -> None:
        created = client.post("/api/v1/leads", json=strong_payload)
        lead_id = created.json()["id"]
        resp = client.post(f"/api/v1/leads/{lead_id}/follow-up-draft", json={"use_ai": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["lead_id"] == lead_id
        assert data["ai_used"] is False
        assert data["fallback_used"] is True
        assert data["subject"]
        assert data["body"]

    def test_follow_up_endpoint_unknown_lead(self, client) -> None:
        resp = client.post(
            "/api/v1/leads/00000000-0000-0000-0000-000000000000/follow-up-draft",
            json={"use_ai": False},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "lead_not_found"

    def test_follow_up_endpoint_friendly_tone(self, client, strong_payload) -> None:
        created = client.post("/api/v1/leads", json=strong_payload)
        lead_id = created.json()["id"]
        resp = client.post(
            f"/api/v1/leads/{lead_id}/follow-up-draft",
            json={"use_ai": False, "tone": "friendly"},
        )
        assert resp.status_code == 200
        assert "Alex" in resp.json()["body"]
