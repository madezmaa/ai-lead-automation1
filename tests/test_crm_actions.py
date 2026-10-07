"""Tests for recommended actions, the CRM-ready record and the notification log."""

from __future__ import annotations

import uuid

from app.domain import Decision, action_for_decision


class TestActionForDecision:
    def test_maps_each_decision(self) -> None:
        assert action_for_decision(Decision.QUALIFIED) == "sales_follow_up"
        assert action_for_decision(Decision.NURTURE) == "add_to_nurture"
        assert action_for_decision(Decision.DISQUALIFIED) == "disqualify"

    def test_accepts_plain_strings_and_falls_back(self) -> None:
        assert action_for_decision("qualified") == "sales_follow_up"
        assert action_for_decision("unknown-decision") == "manual_review"


class TestRecommendedAction:
    def test_qualified_lead_gets_sales_action(self, client, strong_payload) -> None:
        lead = client.post("/api/v1/leads", json=strong_payload).json()
        body = client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": False}).json()
        assert body["decision"] == "qualified"
        assert body["recommended_action"] == "sales_follow_up"

    def test_disqualified_lead_gets_disqualify_action(self, client, weak_payload) -> None:
        lead = client.post("/api/v1/leads", json=weak_payload).json()
        body = client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": False}).json()
        assert body["decision"] == "disqualified"
        assert body["recommended_action"] == "disqualify"

    def test_action_persisted_in_history(self, client, strong_payload) -> None:
        lead = client.post("/api/v1/leads", json=strong_payload).json()
        client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": False})
        history = client.get(f"/api/v1/leads/{lead['id']}/qualifications").json()
        assert history["items"][0]["recommended_action"] == "sales_follow_up"


class TestCrmRecord:
    def test_409_before_qualification(self, client, strong_payload) -> None:
        lead = client.post("/api/v1/leads", json=strong_payload).json()
        response = client.get(f"/api/v1/leads/{lead['id']}/crm-record")
        assert response.status_code == 409
        assert response.json()["code"] == "not_qualified"

    def test_404_for_unknown_lead(self, client) -> None:
        response = client.get(f"/api/v1/leads/{uuid.uuid4()}/crm-record")
        assert response.status_code == 404
        assert response.json()["code"] == "lead_not_found"

    def test_record_after_qualification(self, client, strong_payload) -> None:
        lead = client.post("/api/v1/leads", json=strong_payload).json()
        client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": False})

        response = client.get(f"/api/v1/leads/{lead['id']}/crm-record")
        assert response.status_code == 200
        body = response.json()
        assert body["lead_id"] == lead["id"]
        record = body["record"]
        assert record["external_id"] == f"lead-{lead['id']}"
        assert record["contact"]["email"] == "alex.johnson@acme.io"
        assert record["company"]["name"] == "Acme Inc"
        assert record["company"]["declared_budget"] == 20000.0
        assert record["qualification"]["decision"] == "qualified"
        assert record["qualification"]["recommended_action"] == "sales_follow_up"
        assert record["qualification"]["qualified_at"]


class TestNotificationLog:
    def test_empty_before_qualification(self, client, strong_payload) -> None:
        lead = client.post("/api/v1/leads", json=strong_payload).json()
        response = client.get(f"/api/v1/leads/{lead['id']}/notifications")
        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0}

    def test_skipped_without_webhook_url(self, client, strong_payload) -> None:
        lead = client.post("/api/v1/leads", json=strong_payload).json()
        client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": False})

        log = client.get(f"/api/v1/leads/{lead['id']}/notifications").json()
        assert log["total"] == 1
        entry = log["items"][0]
        assert entry["status"] == "skipped"
        assert entry["event"] == "lead.qualified"
        assert entry["target"] is None
        assert entry["error"] is None

    def test_delivered_when_webhook_succeeds(self, client, strong_payload, monkeypatch) -> None:
        import app.routers.leads as leads_module

        monkeypatch.setattr(leads_module, "send_webhook", lambda *a, **k: True)
        monkeypatch.setattr(client.app.state.settings, "notify_webhook_url", "http://hook.local/q")

        lead = client.post("/api/v1/leads", json=strong_payload).json()
        client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": False})

        log = client.get(f"/api/v1/leads/{lead['id']}/notifications").json()
        entry = log["items"][0]
        assert entry["status"] == "delivered"
        assert entry["target"] == "http://hook.local/q"
        assert entry["error"] is None

    def test_failed_webhook_records_error(self, client, strong_payload, monkeypatch) -> None:
        import app.routers.leads as leads_module

        def failing_send(url, payload, **kwargs):
            on_error = kwargs.get("on_error")
            if on_error is not None:
                on_error("HTTP 500 from webhook: boom")
            return False

        monkeypatch.setattr(leads_module, "send_webhook", failing_send)
        monkeypatch.setattr(client.app.state.settings, "notify_webhook_url", "http://hook.local/q")

        lead = client.post("/api/v1/leads", json=strong_payload).json()
        response = client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": False})
        assert response.status_code == 200

        log = client.get(f"/api/v1/leads/{lead['id']}/notifications").json()
        entry = log["items"][0]
        assert entry["status"] == "failed"
        assert "HTTP 500" in entry["error"]

    def test_qualification_payload_carries_action(
        self, client, strong_payload, monkeypatch
    ) -> None:
        import app.routers.leads as leads_module

        captured: list[dict] = []
        monkeypatch.setattr(
            leads_module, "send_webhook", lambda url, payload, **k: captured.append(payload) or True
        )
        monkeypatch.setattr(client.app.state.settings, "notify_webhook_url", "http://hook.local/q")

        lead = client.post("/api/v1/leads", json=strong_payload).json()
        client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": False})
        assert captured[0]["qualification"]["recommended_action"] == "sales_follow_up"
