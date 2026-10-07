"""Tests for outbound webhook notifications."""

from __future__ import annotations

import httpx

from app.notifications import qualification_payload, send_webhook


def test_send_webhook_success() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = request.read()
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert send_webhook("http://hook.local/x", {"event": "e"}, client=client) is True
    assert seen["url"] == "http://hook.local/x"


def test_send_webhook_rejected_status_returns_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert send_webhook("http://hook.local/x", {"a": 1}, client=client) is False


def test_send_webhook_never_raises_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert send_webhook("http://hook.local/x", {"a": 1}, client=client) is False


def test_qualification_payload_shape() -> None:
    payload = qualification_payload(
        event="lead.qualified",
        lead_id="abc",
        email="a@b.io",
        status="qualified",
        decision="qualified",
        score=80,
        reason="fit",
        ai_used=True,
        fallback_used=False,
        source="webhook",
    )
    assert payload["event"] == "lead.qualified"
    assert payload["lead"]["email"] == "a@b.io"
    assert payload["qualification"]["score"] == 80
    assert payload["qualification"]["ai_used"] is True


def test_qualify_endpoint_sends_notification(client, strong_payload, monkeypatch) -> None:
    captured: list[dict] = []

    import app.routers.leads as leads_module

    def fake_send(url: str, payload: dict, **kwargs) -> bool:
        captured.append({"url": url, "payload": payload})
        return True

    monkeypatch.setattr(leads_module, "send_webhook", fake_send)
    monkeypatch.setattr(client.app.state.settings, "notify_webhook_url", "http://hook.local/q")

    created = client.post("/api/v1/leads", json=strong_payload)
    lead_id = created.json()["id"]
    qualified = client.post(f"/api/v1/leads/{lead_id}/qualify", json={"use_ai": False})
    assert qualified.status_code == 200

    assert len(captured) == 1
    entry = captured[0]
    assert entry["url"] == "http://hook.local/q"
    assert entry["payload"]["event"] == "lead.qualified"
    assert entry["payload"]["lead"]["id"] == lead_id
    assert entry["payload"]["qualification"]["decision"] == "qualified"


def test_no_notification_without_webhook_url(client, strong_payload, monkeypatch) -> None:
    import app.routers.leads as leads_module

    calls: list[dict] = []
    monkeypatch.setattr(leads_module, "send_webhook", lambda *a, **k: calls.append(a) or True)
    created = client.post("/api/v1/leads", json=strong_payload)
    lead_id = created.json()["id"]
    assert (
        client.post(f"/api/v1/leads/{lead_id}/qualify", json={"use_ai": False}).status_code == 200
    )
    assert calls == []
