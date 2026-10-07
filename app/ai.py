"""Ollama AI qualification layer with structured output.

Calls the local Ollama server's chat endpoint with ``format="json"``, parses
and strictly validates the structured result, and raises
:class:`AIQualificationError` on any failure so the caller can fall back to
the deterministic rules engine.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings
from app.domain import AIResult, Decision, LeadProfile

logger = logging.getLogger(__name__)

DecisionLiteral = Literal["qualified", "nurture", "disqualified"]

_SYSTEM_PROMPT_TEMPLATE = """\
You are a B2B lead qualification engine for a company that sells AI-powered
sales workflow software. Score each lead from 0 to 100, where 100 is a perfect
fit and 0 is not a fit at all.

Decision rules:
- score >= {qualified}: "qualified"   (sales-ready, act now)
- score >= {nurture}: "nurture"       (medium fit, keep in touch)
- otherwise: "disqualified"           (not worth pursuing)

Consider these signals, in order of importance:
1. Budget (higher = stronger).
2. Company size (earlier = stronger).
3. Buying intent visible in the free-text message (demo, pricing, quote,
   integration, procurement, timeline, existing vendor).
4. Job seniority of the contact.
5. Whether the contact uses a business email.
6. Fit with target industry and country.

Be decisive and stick to the scoring rules. Respond with ONLY a JSON object,
no prose, no markdown, matching exactly this shape:
{{
  "score": <integer 0-100>,
  "decision": "qualified" | "nurture" | "disqualified",
  "reason": "<one concise sentence explaining the score>",
  "signals": ["<short signal>", "<short signal>"]
}}
Example:
{{"score": 82, "decision": "qualified", "reason": "Enterprise budget, senior
buyer, explicit demo request.",
"signals": ["enterprise budget", "senior title", "demo request"]}}
"""


class AIQualificationOutput(BaseModel):
    """Strict schema for the JSON the model is asked to produce."""

    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=100)
    decision: DecisionLiteral
    reason: str = Field(min_length=1, max_length=1000, description="one sentence")
    signals: list[str] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def _coerce_score(cls, value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("score must be a number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("score must be a number") from exc
        return int(round(min(100.0, max(0.0, number))))

    @field_validator("reason", "signals", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class Qualifier(Protocol):
    """Interface the service layer uses for any AI qualifier."""

    def qualify(self, profile: LeadProfile) -> AIResult: ...


class AIQualificationError(Exception):
    """Raised when the AI layer cannot produce a valid structured result.

    ``retryable`` marks transient failures (timeouts, transport/protocol
    errors) that are worth retrying; schema violations are deterministic at
    temperature 0 and are not retried.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class FollowUpDraft(BaseModel):
    """A follow-up email draft for a lead."""

    model_config = ConfigDict(extra="ignore")

    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=5000)


class AIDraftError(Exception):
    """Raised when the AI layer cannot produce a valid follow-up draft."""


_FOLLOWUP_SYSTEM_PROMPT = """\
You write short, effective B2B follow-up emails to inbound leads.
Rules:
- Friendly, professional tone; never pushy.
- At most 120 words in the body.
- Reference the lead's company and what they asked about when known.
- End with one clear next step (a short call or a demo).
Respond with ONLY a JSON object, no prose, no markdown, matching exactly:
{{"subject": "<max 120 chars>", "body": "<the email body>"}}
"""

_FOLLOWUP_TEMPLATE = """\
Hi {name},

Thanks for reaching out{context} — I'd love to help.

I've had a look at what you're after and it looks like a good fit for how we
work with teams like {company_hint}. Two things I can do for you right now:
send over tailored pricing, or walk you through the product in a 20-minute
call.

Would sometime this week work for a short chat? Just reply with a time that
suits you and I'll send an invite.

Best regards,
The Sales Team"""


def render_follow_up_template(
    profile: LeadProfile,
    *,
    tone: str = "professional",
) -> FollowUpDraft:
    """Deterministic follow-up draft used when AI is disabled or unavailable."""
    name = (profile.first_name or profile.company or "there").strip()
    context = f" about {profile.company}" if profile.company else ""
    company_hint = profile.company or "your team"
    body = _FOLLOWUP_TEMPLATE.format(name=name, context=context, company_hint=company_hint)
    if tone == "friendly":
        body = body.replace("Best regards,\nThe Sales Team", "Cheers,\nThe Sales Team")
        subject = f"Quick follow-up, {name}!"
    else:
        subject = f"Following up{context}"
    return FollowUpDraft(subject=subject, body=body)


class OllamaQualifier:
    """Lives on top of the Ollama chat API with ``format="json"``."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float = 30.0,
        max_tokens: int = 512,
        qualified_threshold: int = 70,
        nurture_threshold: int = 40,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.qualified_threshold = qualified_threshold
        self.nurture_threshold = nurture_threshold
        self.max_retries = max(1, max_retries)
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout))

    @classmethod
    def from_settings(cls, settings: Settings) -> OllamaQualifier:
        return cls(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout_seconds,
            max_tokens=settings.ollama_max_tokens,
            qualified_threshold=settings.qualified_threshold,
            nurture_threshold=settings.nurture_threshold,
            max_retries=settings.ollama_max_retries,
        )

    def close(self) -> None:
        self._client.close()

    def qualify(self, profile: LeadProfile) -> AIResult:
        """Qualify a lead and return a validated structured result.

        Retries transient transport failures up to ``max_retries`` attempts.
        Raises :class:`AIQualificationError` on any connectivity, protocol or
        schema failure.
        """
        content = self._chat_with_retries(self._system_prompt(), self._user_prompt(profile))
        payload = self._parse_and_validate(content)

        return AIResult(
            score=payload.score,
            decision=Decision(payload.decision),
            reason=payload.reason,
            signals=tuple(payload.signals),
            model=self.model,
        )

    def draft_follow_up(
        self,
        profile: LeadProfile,
        *,
        tone: str = "professional",
        use_ai: bool = True,
    ) -> tuple[FollowUpDraft, bool]:
        """Return ``(draft, ai_used)``; falls back to the template on any AI error."""
        if not use_ai:
            return render_follow_up_template(profile, tone=tone), False
        try:
            content = self._chat_with_retries(
                _FOLLOWUP_SYSTEM_PROMPT, self._draft_prompt(profile, tone)
            )
            draft = FollowUpDraft.model_validate(self._load_json_object(content))
        except (AIQualificationError, AIDraftError, ValueError) as exc:
            logger.warning("AI follow-up draft failed; using template: %s", exc)
            return render_follow_up_template(profile, tone=tone), False
        return draft, True

    def _chat_with_retries(self, system: str, user: str) -> str:
        last: AIQualificationError | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return self._chat(system, user)
            except httpx.TimeoutException as exc:
                last = AIQualificationError(f"Ollama request timed out: {exc}", retryable=True)
            except httpx.HTTPError as exc:
                last = AIQualificationError(f"Ollama request failed: {exc}", retryable=True)
            except AIQualificationError as exc:
                last = exc
            if last is not None and (not last.retryable or attempt >= self.max_retries):
                raise last
            logger.warning(
                "Ollama attempt %d/%d failed (retryable): %s",
                attempt,
                self.max_retries,
                last,
            )
            time.sleep(0.2 * attempt)
        raise last if last is not None else AIQualificationError("Ollama call failed")

    def _chat(self, system: str, user: str) -> str:
        response = self._client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0, "num_predict": self.max_tokens},
            },
        )
        if response.status_code != 200:
            raise AIQualificationError(
                f"Ollama returned HTTP {response.status_code}: {response.text[:200]}",
                retryable=response.status_code >= 500,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise AIQualificationError(
                f"Ollama returned a non-JSON body: {exc}", retryable=True
            ) from exc
        content = (data.get("message") or {}).get("content")
        if not content:
            raise AIQualificationError(
                "Ollama response contained no message content", retryable=True
            )
        return content

    @staticmethod
    def _load_json_object(content: str) -> dict:
        cleaned = re_strip_fences(content.strip())
        try:
            raw = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise AIDraftError(f"Ollama returned invalid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise AIDraftError("Ollama JSON payload was not an object")
        return raw

    def _parse_and_validate(self, content: str) -> AIQualificationOutput:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re_strip_fences(cleaned)
        try:
            raw = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise AIQualificationError(f"Ollama returned invalid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise AIQualificationError("Ollama JSON payload was not an object")
        try:
            return AIQualificationOutput.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - surface as AI error
            raise AIQualificationError(f"Ollama output failed schema validation: {exc}") from exc

    def _system_prompt(self) -> str:
        return _SYSTEM_PROMPT_TEMPLATE.format(
            qualified=self.qualified_threshold,
            nurture=self.nurture_threshold,
        )

    @staticmethod
    def _user_prompt(profile: LeadProfile) -> str:
        fields = [
            "email",
            "first_name",
            "last_name",
            "company",
            "job_title",
            "phone",
            "website",
            "industry",
            "country",
            "country_code",
            "source",
            "message",
            "budget",
            "company_size",
        ]
        lines = []
        for field in fields:
            value = profile.as_dict().get(field)
            if value is None or value == "":
                value = "unknown"
            lines.append(f"{field}: {value}")
        return "Lead details:\n" + "\n".join(lines)

    @staticmethod
    def _draft_prompt(profile: LeadProfile, tone: str) -> str:
        lines = [
            f"tone: {tone}",
            f"first_name: {profile.first_name or 'unknown'}",
            f"company: {profile.company or 'unknown'}",
            f"job_title: {profile.job_title or 'unknown'}",
            f"industry: {profile.industry or 'unknown'}",
            f"message: {profile.message or 'unknown'}",
        ]
        return "Follow-up email for this lead:\n" + "\n".join(lines)


def re_strip_fences(content: str) -> str:
    """Strip a surrounding ```json ... ``` code block if present."""
    if content.startswith("```"):
        first_newline = content.find("\n")
        if first_newline != -1:
            content = content[first_newline + 1 :]
    if content.rstrip().endswith("```"):
        content = content.rstrip()[:-3]
    return content.strip()
