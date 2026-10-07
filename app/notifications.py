"""Outbound notification webhooks (best-effort, never fail the request)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3.0


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
