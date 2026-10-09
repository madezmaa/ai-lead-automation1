"""Outbound notifications (webhook + speed-to-lead email, never fail the request)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3.0

# Single transactional email provider (Resend HTTP API).
EMAIL_API_URL = "https://api.resend.com/emails"


def send_webhook(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
    on_error: Callable[[str], None] | None = None,
) -> bool:
    """POST ``payload`` to ``url``. Returns True on a 2xx response.

    Never raises: network errors, timeouts and non-2xx statuses are logged and
    reported as ``False`` so notifications can never break the request flow.
    When ``on_error`` is given it receives a human-readable failure reason.
    """
    owned = client is None
    http = client or httpx.Client()
    try:
        response = http.post(url, json=payload, timeout=timeout)
        if 200 <= response.status_code < 300:
            logger.info("Notification delivered to %s (%d)", url, response.status_code)
            return True
        reason = f"HTTP {response.status_code} from webhook: {response.text[:200]}"
        logger.warning("Notification to %s rejected - %s", url, reason)
        if on_error is not None:
            on_error(reason)
        return False
    except httpx.HTTPError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("Notification to %s failed - %s", url, reason)
        if on_error is not None:
            on_error(reason)
        return False
    finally:
        if owned:
            http.close()


def send_email(
    *,
    api_key: str | None,
    sender: str,
    to: str,
    subject: str,
    body: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
    on_error: Callable[[str], None] | None = None,
) -> bool:
    """Send a plain-text email through the Resend HTTP API.

    Same contract as :func:`send_webhook`: returns True on a 2xx response and
    never raises, so a delivery failure can never break the request flow.
    When ``on_error`` is given it receives a human-readable failure reason.
    """
    if not api_key:
        reason = "EMAIL_API_KEY is not configured"
        logger.warning("Email to %s skipped - %s", to, reason)
        if on_error is not None:
            on_error(reason)
        return False

    payload = {"from": sender, "to": [to], "subject": subject, "text": body}
    headers = {"Authorization": f"Bearer {api_key}"}
    owned = client is None
    http = client or httpx.Client()
    try:
        response = http.post(EMAIL_API_URL, json=payload, headers=headers, timeout=timeout)
        if 200 <= response.status_code < 300:
            logger.info("Email delivered to %s (%d)", to, response.status_code)
            return True
        reason = f"HTTP {response.status_code} from email provider: {response.text[:200]}"
        logger.warning("Email to %s rejected - %s", to, reason)
        if on_error is not None:
            on_error(reason)
        return False
    except httpx.HTTPError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("Email to %s failed - %s", to, reason)
        if on_error is not None:
            on_error(reason)
        return False
    finally:
        if owned:
            http.close()


def qualification_payload(
    *,
    event: str,
    lead_id: str,
    email: str,
    status: str,
    decision: str,
    score: int,
    reason: str,
    ai_used: bool,
    fallback_used: bool,
    source: str,
    recommended_action: str = "",
) -> dict[str, Any]:
    """Canonical notification body for a completed qualification."""
    return {
        "event": event,
        "lead": {
            "id": lead_id,
            "email": email,
            "source": source,
            "status": status,
        },
        "qualification": {
            "decision": decision,
            "score": score,
            "recommended_action": recommended_action,
            "reason": reason,
            "ai_used": ai_used,
            "fallback_used": fallback_used,
        },
    }
