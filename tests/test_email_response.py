"""Tests for the automatic speed-to-lead reply email."""

from __future__ import annotations

import httpx

from app.notifications import send_email

DRY_RUN_NOTE = "DRY_RUN"


def _qualify(client, payload: dict) -> tuple[str, dict]:
    created = client.post("/api/v1/leads", json=payload)
    assert created.status_code == 201
    lead = created.json()
    result = client.post(f"/api/v1/leads/{lead['id']}/qualify", json={"use_ai": False})
    assert result.status_code == 200
    return lead["email"], result.json()


def _email_rows(client, lead_id: str) -> list[dict]:
    items = client.get(f"/api/v1/leads/{lead_id}/notifications").json()["items"]
    return [item for item in items if item["channel"] == "email"]


def test_send_email_success() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = request.read()
        return httpx.Response(200, json={"id": "email_1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        delivered = send_email(
            api_key="re_test_key",
            sender="leads@acme.io",
            to="dana@prospect.io",
            subject="Quick follow-up, Dana!",
            body="Hello Dana",
            client=client,
        )
    assert delivered is True
    assert seen["url"] == "https://api.resend.com/emails"
    assert seen["auth"] == "Bearer re_test_key"
    assert b'"dana@prospect.io"' in seen["body"]
    assert b"Quick follow-up" in seen["body"]


def test_send_email_rejected_status_returns_false_and_reports_reason() -> None:
    reasons: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        delivered = send_email(
            api_key="re_test_key",
            sender="leads@acme.io",
            to="dana@prospect.io",
            subject="Hi",
            body="Hello",
            client=client,
            on_error=reasons.append,
        )
    assert delivered is False
    assert reasons and "HTTP 500" in reasons[0]


def test_send_email_without_api_key_never_calls_the_provider() -> None:
    reasons: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP call expected without an API key")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        delivered = send_email(
            api_key=None,
            sender="leads@acme.io",
            to="dana@prospect.io",
            subject="Hi",
            body="Hello",
            client=client,
            on_error=reasons.append,
        )
    assert delivered is False
    assert reasons and "EMAIL_API_KEY" in reasons[0]


def test_dry_run_records_skipped_reply_without_sending(client, strong_payload, monkeypatch) -> None:
    calls: list[dict] = []
    import app.routers.leads as leads_module

    monkeypatch.setattr(leads_module, "send_email", lambda **kwargs: calls.append(kwargs) or True)

    email, result = _qualify(client, strong_payload)
    assert result["decision"] == "qualified"
    assert calls == []

    rows = _email_rows(client, result["lead_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "skipped"
    assert rows[0]["target"] == email
    assert DRY_RUN_NOTE in rows[0]["error"]


def test_reply_is_sent_when_dry_run_is_disabled(
    test_client_factory, strong_payload, monkeypatch
) -> None:
    calls: list[dict] = []
    import app.routers.leads as leads_module

    def fake_send_email(**kwargs) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr(leads_module, "send_email", fake_send_email)
    client = test_client_factory(
        dry_run=False,
        email_api_key="re_live_key",
        email_from="bots@acme.io",
    )

    email, result = _qualify(client, strong_payload)
    assert result["decision"] == "qualified"
    assert len(calls) == 1
    sent = calls[0]
    assert sent["api_key"] == "re_live_key"
    assert sent["sender"] == "bots@acme.io"
    assert sent["to"] == email
    assert sent["subject"]
    assert sent["body"]

    rows = _email_rows(client, result["lead_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "delivered"
    assert rows[0]["error"] is None


def test_failed_delivery_is_logged_as_failed(
    test_client_factory, strong_payload, monkeypatch
) -> None:
    import app.routers.leads as leads_module

    def fake_send_email(**kwargs) -> bool:
        on_error = kwargs.get("on_error")
        if on_error:
            on_error("HTTP 500 from email provider: boom")
        return False

    monkeypatch.setattr(leads_module, "send_email", fake_send_email)
    client = test_client_factory(dry_run=False, email_api_key="re_live_key")

    _, result = _qualify(client, strong_payload)
    rows = _email_rows(client, result["lead_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert "HTTP 500" in rows[0]["error"]


def test_opted_out_lead_is_never_emailed(client, strong_payload) -> None:
    payload = {
        **strong_payload,
        "email": "dana@prospect.io",
        "message": "Please unsubscribe me and stop emailing this address.",
    }
    _, result = _qualify(client, payload)
    assert result["decision"] == "disqualified"
    assert _email_rows(client, result["lead_id"]) == []
