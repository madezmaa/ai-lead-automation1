"""Live integration test against a real Ollama server.

Marked ``ollama``: any ``pytest -m 'not ollama'`` run skips these. The test
verifies the structured-output contract end to end (connectivity + valid JSON
+ our schema validation).
"""

from __future__ import annotations

import httpx
import pytest

from app.ai import AIQualificationError, OllamaQualifier
from app.config import get_settings
from app.domain import AIResult, Decision, LeadProfile

pytestmark = pytest.mark.ollama


def _ollama_available() -> tuple[bool, str]:
    settings = get_settings()
    if not settings.ollama_enabled:
        return False, "OLLAMA_ENABLED=false"
    try:
        response = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0)
    except httpx.HTTPError as exc:
        return False, f"Ollama not reachable at {settings.ollama_base_url}: {exc}"
    if response.status_code != 200:
        return False, f"Ollama returned HTTP {response.status_code}"
    models = [item.get("name") for item in response.json().get("models", [])]
    if settings.ollama_model not in models:
        return False, f"Model {settings.ollama_model!r} not pulled (have: {models})"
    return True, ""


def _strong_profile() -> LeadProfile:
    return LeadProfile(
        email="alex.johnson@acme.io",
        first_name="Alex",
        last_name="Johnson",
        company="Acme Inc",
        job_title="VP Sales",
        phone="+14155552671",
        website="https://acme.io",
        industry="saas",
        country="United States",
        country_code="US",
        source="referral",
        message="We are evaluating a demo and pricing for Q3.",
        budget=20000.0,
        company_size=250,
    )


def test_live_ai_qualification() -> None:
    available, reason = _ollama_available()
    if not available:
        pytest.skip(reason)

    settings = get_settings()
    qualifier = OllamaQualifier.from_settings(settings)
    try:
        result = qualifier.qualify(_strong_profile())
    except AIQualificationError as exc:
        pytest.fail(f"Live Ollama qualification failed the structured contract: {exc}")

    assert isinstance(result, AIResult)
    assert 0 <= result.score <= 100
    assert result.decision in (Decision.QUALIFIED, Decision.NURTURE, Decision.DISQUALIFIED)
    assert result.reason
    assert result.model == settings.ollama_model
