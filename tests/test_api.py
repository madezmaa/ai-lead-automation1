"""End-to-end API tests."""

from __future__ import annotations

import uuid

from app.ai import OllamaQualifier
from app.domain import AIResult, Decision


class TestHealth:
    def test_health_ok(self, client) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "up"
        assert body["ollama"] == {"enabled": False}


class TestCreateAndRead:
    def test_create_lead(self, client, strong_payload: dict) -> None:
        response = client.post("/api/v1/leads", json=strong_payload)
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "alex.johnson@acme.io"
        assert body["status"] == "new"
        assert body["country"] == "United States"
        assert body["country_code"] == "US"
        assert body["phone"] == "+14155552671"
        assert body["website"] == "https://acme.io"
        uuid.UUID(body["id"])

    def test_duplicate_email_rejected(self, client, strong_payload: dict) -> None:
        client.post("/api/v1/leads", json=strong_payload)
        response = client.post("/api/v1/leads", json=strong_payload)
        assert response.status_code == 409
        assert response.json()["code"] == "duplicate_lead"

    def test_invalid_email_rejected(self, client) -> None:
        response = client.post("/api/v1/leads", json={"email": "not-an-email"})
        assert response.status_code == 422

    def test_get_lead(self, client, strong_payload: dict) -> None:
        created = client.post("/api/v1/leads", json=strong_payload).json()
        response = client.get(f"/api/v1/leads/{created['id']}")
        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    def test_get_missing_lead(self, client) -> None:
        response = client.get(f"/api/v1/leads/{uuid.uuid4()}")
        assert response.status_code == 404
        assert response.json()["code"] == "lead_not_found"


class TestList:
    def test_list_and_filters(self, client, strong_payload: dict, weak_payload: dict) -> None:
        client.post("/api/v1/leads", json=strong_payload)
        client.post("/api/v1/leads", json=weak_payload)

        all_leads = client.get("/api/v1/leads")
        assert all_leads.status_code == 200
        assert all_leads.json()["total"] == 2

        by_source = client.get("/api/v1/leads", params={"source": "referral"})
        assert by_source.json()["total"] == 1
        assert by_source.json()["items"][0]["email"] == "alex.johnson@acme.io"

        search = client.get("/api/v1/leads", params={"q": "gmail"})
        assert search.json()["total"] == 1
        assert search.json()["items"][0]["email"] == "jane.smith@gmail.com"

    def test_pagination(self, client, strong_payload: dict) -> None:
        for index in range(3):
            client.post("/api/v1/leads", json={**strong_payload, "email": f"u{index}@acme.io"})
        page = client.get("/api/v1/leads", params={"limit": 1, "offset": 0})
        assert page.json()["total"] == 3
        assert len(page.json()["items"]) == 1

    def test_invalid_status_filter(self, client) -> None:
        response = client.get("/api/v1/leads", params={"status": "not-a-status"})
        assert response.status_code == 422

    def test_bad_pagination_bounds(self, client) -> None:
        response = client.get("/api/v1/leads", params={"limit": 0})
        assert response.status_code == 422


class TestQualify:
    def test_qualify_with_rules_only(self, client, strong_payload: dict) -> None:
        lead = client.post("/api/v1/leads", json=strong_payload).json()
        response = client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": True})
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "qualified"
        assert body["score"] == 100
        assert body["ai_used"] is False
        assert body["fallback_used"] is True
        assert body["state_from"] == "new"
        assert body["state_to"] == "qualified"
        assert set(body["rules_output"]) == {
            "score",
            "decision",
            "factors",
            "hard_disqualified",
            "hard_reason",
            "summary",
        }

        lead_after = client.get(f"/api/v1/leads/{lead['id']}").json()
        assert lead_after["status"] == "qualified"

        history = client.get(f"/api/v1/leads/{lead['id']}/qualifications")
        assert history.status_code == 200
        assert history.json()["total"] == 1

    def test_qualify_weak_lead_disqualified(self, client, weak_payload: dict) -> None:
        lead = client.post("/api/v1/leads", json=weak_payload).json()
        response = client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": False})
        assert response.status_code == 200
        assert response.json()["decision"] == "disqualified"

    def test_qualify_missing_lead(self, client) -> None:
        response = client.post(f"/api/v1/leads/{uuid.uuid4()}/qualify")
        assert response.status_code == 404

    def test_ai_outage_falls_back_to_rules(self, test_client_factory, strong_payload: dict) -> None:
        with test_client_factory(
            ollama_enabled=True, ollama_base_url="http://127.0.0.1:1", ollama_timeout_seconds=2
        ) as client:
            lead = client.post("/api/v1/leads", json=strong_payload).json()
            response = client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": True})
            assert response.status_code == 200
            body = response.json()
            assert body["ai_used"] is False
            assert body["fallback_used"] is True
            assert body["decision"] == "qualified"
            assert body["ai_output"] is None

    def test_ai_blends_with_rules(
        self, test_client_factory, strong_payload: dict, monkeypatch
    ) -> None:
        def fake_qualify(self: OllamaQualifier, profile):
            return AIResult(
                score=20,
                decision=Decision.DISQUALIFIED,
                reason="AI says no",
                signals=(),
                model=self.model,
            )

        monkeypatch.setattr(OllamaQualifier, "qualify", fake_qualify)
        with test_client_factory(ollama_enabled=True, ollama_base_url="http://ollama") as client:
            lead = client.post("/api/v1/leads", json=strong_payload).json()
            response = client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": True})
            assert response.status_code == 200
            body = response.json()
            assert body["ai_used"] is True
            assert body["fallback_used"] is False
            # rules=100, ai=20, weight=0.6 -> round(100*0.4 + 20*0.6) = 52 -> nurture
            assert body["score"] == 52
            assert body["decision"] == "nurture"
            assert body["ai_output"]["reason"] == "AI says no"

    def test_ai_crash_marks_lead_failed(
        self, test_client_factory, strong_payload: dict, monkeypatch
    ) -> None:
        def exploding(self: OllamaQualifier, profile):
            raise RuntimeError("boom")

        monkeypatch.setattr(OllamaQualifier, "qualify", exploding)
        with test_client_factory(
            {"raise_server_exceptions": False},
            ollama_enabled=True,
            ollama_base_url="http://ollama",
        ) as client:
            lead = client.post("/api/v1/leads", json=strong_payload).json()
            response = client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": True})
            assert response.status_code == 500
            lead_after = client.get(f"/api/v1/leads/{lead['id']}").json()
            assert lead_after["status"] == "failed"

    def test_re_qualification(self, client, mid_payload: dict) -> None:
        lead = client.post("/api/v1/leads", json=mid_payload).json()
        first = client.post(f"/api/v1/leads/{lead['id']}/qualify")
        second = client.post(f"/api/v1/leads/{lead['id']}/qualify")
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["state_from"] == "nurture"
        history = client.get(f"/api/v1/leads/{lead['id']}/qualifications")
        assert history.json()["total"] == 2


class TestStatusTransitions:
    def test_manual_transition_roundtrip(self, client, strong_payload: dict) -> None:
        lead = client.post("/api/v1/leads", json=strong_payload).json()
        archived = client.patch(
            f"/api/v1/leads/{lead['id']}/status", json={"status": "archived", "reason": "dup"}
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"
        restored = client.patch(f"/api/v1/leads/{lead['id']}/status", json={"status": "new"})
        assert restored.status_code == 200
        assert restored.json()["status"] == "new"

    def test_illegal_transition_rejected(self, client, strong_payload: dict) -> None:
        lead = client.post("/api/v1/leads", json=strong_payload).json()
        response = client.patch(f"/api/v1/leads/{lead['id']}/status", json={"status": "qualified"})
        assert response.status_code == 409
        assert response.json()["code"] == "invalid_transition"


class TestAuth:
    def test_api_key_required_when_configured(
        self, test_client_factory, strong_payload: dict
    ) -> None:
        with test_client_factory(api_key="secret-key") as client:
            denied = client.post("/api/v1/leads", json=strong_payload)
            assert denied.status_code == 401

            allowed = client.post(
                "/api/v1/leads", json=strong_payload, headers={"X-API-Key": "secret-key"}
            )
            assert allowed.status_code == 201

    def test_health_is_open_without_key(self, test_client_factory) -> None:
        with test_client_factory(api_key="secret-key") as client:
            assert client.get("/health").status_code == 200


class TestCors:
    """The static demo on :5500 is a separate origin and must be allowed."""

    def test_demo_origin_allowed_on_simple_request(self, client) -> None:
        response = client.get("/health", headers={"Origin": "http://localhost:5500"})
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5500"

    def test_unknown_origin_gets_no_cors_header(self, client) -> None:
        response = client.get("/health", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in response.headers

    def test_preflight_allows_demo_post(self, client) -> None:
        response = client.options(
            "/api/v1/leads",
            headers={
                "Origin": "http://localhost:5500",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5500"
        assert "POST" in response.headers.get("access-control-allow-methods", "")
