"""Tests for Idempotency-Key support on lead creation."""

from __future__ import annotations


class TestIdempotencyKey:
    def test_first_request_creates_201(self, client, strong_payload) -> None:
        resp = client.post(
            "/api/v1/leads",
            json=strong_payload,
            headers={"Idempotency-Key": "key-123"},
        )
        assert resp.status_code == 201
        assert resp.json()["id"]

    def test_replay_returns_same_lead_with_200(self, client, strong_payload) -> None:
        first = client.post(
            "/api/v1/leads",
            json=strong_payload,
            headers={"Idempotency-Key": "key-123"},
        )
        second = client.post(
            "/api/v1/leads",
            json=strong_payload,
            headers={"Idempotency-Key": "key-123"},
        )
        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

        listing = client.get("/api/v1/leads").json()
        assert listing["total"] == 1

    def test_replay_with_modified_body_returns_original_lead(self, client, strong_payload) -> None:
        first = client.post(
            "/api/v1/leads",
            json=strong_payload,
            headers={"Idempotency-Key": "key-456"},
        )
        changed = client.post(
            "/api/v1/leads",
            json={**strong_payload, "company": "Renamed Co"},
            headers={"Idempotency-Key": "key-456"},
        )
        assert changed.status_code == 200
        assert changed.json()["company"] == first.json()["company"]

    def test_different_keys_still_hit_duplicate_guard(self, client, strong_payload) -> None:
        client.post(
            "/api/v1/leads",
            json=strong_payload,
            headers={"Idempotency-Key": "key-a"},
        )
        duplicate = client.post(
            "/api/v1/leads",
            json=strong_payload,
            headers={"Idempotency-Key": "key-b"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "duplicate_lead"

    def test_service_level_replay(self, db_session, strong_payload) -> None:
        from app.schemas import LeadCreate
        from app.service import create_lead

        payload = LeadCreate.model_validate(strong_payload)
        lead_a, replayed_a = create_lead(db_session, payload, idempotency_key="svc-1")
        lead_b, replayed_b = create_lead(db_session, payload, idempotency_key="svc-1")
        assert replayed_a is False
        assert replayed_b is True
        assert lead_a.id == lead_b.id
