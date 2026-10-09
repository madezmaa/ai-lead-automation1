"""Regression tests for the two reported customer-demo frontend bugs.

1. Scoring factors: the API must expose the real ``rules_output.factors`` and
   the demo must render them (never invent them).
2. Lead status: a net-zero re-qualification must not be shown as a fabricated
   ``qualified -> qualified`` transition.

The backend-contract checks always run. The two display helpers
(``describeStateTransition`` / ``normalizeFactors``) live in ``demo/app.js`` and
are executed in a real headless browser when one is available; otherwise those
cases are reported as *skipped* rather than silently passed.
"""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS = REPO_ROOT / "demo" / "app.js"


# ---------------------------------------------------------------------------
# Bug 1 - scoring factors (backend contract the UI renders)
# ---------------------------------------------------------------------------


def _create_and_qualify(client, payload: dict, *, use_ai: bool = False):
    created = client.post("/api/v1/leads", json=payload)
    assert created.status_code == 201, created.text
    lead_id = created.json()["id"]
    result = client.post(f"/api/v1/leads/{lead_id}/qualify", json={"use_ai": use_ai})
    assert result.status_code == 200, result.text
    return lead_id, result.json()


def test_qualification_returns_itemized_scoring_factors(client, strong_payload: dict) -> None:
    _lead_id, result = _create_and_qualify(client, strong_payload)

    rules = result["rules_output"]
    factors = rules["factors"]
    assert isinstance(factors, list) and factors, "rules_output.factors must be a non-empty list"

    for factor in factors:
        assert {"name", "points", "detail"} <= set(factor)
        assert isinstance(factor["name"], str) and factor["name"]
        assert isinstance(factor["points"], int) and not isinstance(factor["points"], bool)
        assert isinstance(factor["detail"], str)

    # The deterministic score is the (clamped) sum of the itemized factors.
    total = sum(factor["points"] for factor in factors)
    assert rules["score"] == max(0, min(100, total))

    names = {factor["name"] for factor in factors}
    assert {"budget", "email", "intent"} <= names


def test_scoring_factors_survive_an_empty_optional_lead(client) -> None:
    payload = {"email": "sparse@unknown.example", "message": "hello"}
    _lead_id, result = _create_and_qualify(client, payload)
    factors = result["rules_output"]["factors"]
    assert factors, "factors must still be reported for a sparse lead"
    # No invented data: every factor carries a concrete, non-empty detail.
    assert all(factor["detail"] for factor in factors)


# ---------------------------------------------------------------------------
# Bug 2 - status transition must never be fabricated
# ---------------------------------------------------------------------------


def test_requalification_reports_a_net_zero_transition(client, strong_payload: dict) -> None:
    lead_id, first = _create_and_qualify(client, strong_payload)
    assert first["state_from"] == "new"
    assert first["state_to"] == first["decision"]
    assert first["state_from"] != first["state_to"]

    # Re-qualify the same (already terminal) lead. The previous status already
    # equals the outcome, so the API reports a net-zero move. The UI must show
    # only the current status rather than "qualified -> qualified".
    requalified = client.post(f"/api/v1/leads/{lead_id}/qualify", json={"use_ai": False})
    assert requalified.status_code == 200, requalified.text
    second = requalified.json()
    assert second["state_from"] == first["state_to"]
    assert second["state_to"] == second["decision"]
    assert second["state_from"] == second["state_to"]


# ---------------------------------------------------------------------------
# The display helpers, executed in a real browser
# ---------------------------------------------------------------------------


def _find_chrome() -> str | None:
    candidates = [
        os.environ.get("CHROME_PATH"),
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


CHROME = _find_chrome()


def _run_helpers_in_browser(tmp_path: Path) -> dict:
    script = """
document.addEventListener('DOMContentLoaded', () => {
  const result = {
    equal: describeStateTransition('qualified', 'qualified'),
    change: describeStateTransition('new', 'qualified'),
    missingFrom: describeStateTransition(null, 'qualified'),
    missingTo: describeStateTransition('new', undefined),
    emptyBoth: describeStateTransition(undefined, undefined),
    factors: normalizeFactors({ factors: [
      { name: 'budget', points: 30, detail: 'budget >= $25k' },
      { name: 'intent', points: 15, detail: 'buying intent' },
    ] }),
    factorsEmpty: normalizeFactors({}),
    factorsMalformed: normalizeFactors({ factors: [null, 'junk', { name: 'x' }] }),
  };
  document.getElementById('out').textContent = JSON.stringify(result);
});
"""
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
        '<button id="btn-run"></button><button id="btn-reset"></button>'
        '<form id="lead-form"></form><pre id="out"></pre>'
        f'<script src="{APP_JS.as_uri()}"></script>'
        f"<script>{script}</script></body></html>"
    )
    page = tmp_path / "helpers.html"
    page.write_text(html, encoding="utf-8")

    completed = subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--virtual-time-budget=3000",
            "--dump-dom",
            page.as_uri(),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    match = re.search(r'<pre id="out">(.*?)</pre>', completed.stdout, re.DOTALL)
    assert match, f"helper output not found; stderr={completed.stderr[:500]}"
    return json.loads(html_lib.unescape(match.group(1)))


@pytest.mark.skipif(CHROME is None, reason="headless Chrome/Edge not available")
def test_status_transition_helper_real_browser(tmp_path: Path) -> None:
    out = _run_helpers_in_browser(tmp_path)
    assert out["equal"] == "qualified"
    assert out["change"] == "new → qualified"
    assert out["missingFrom"] == "qualified"
    assert out["missingTo"] == "new → —"
    assert out["emptyBoth"] == "—"


@pytest.mark.skipif(CHROME is None, reason="headless Chrome/Edge not available")
def test_normalize_factors_helper_real_browser(tmp_path: Path) -> None:
    out = _run_helpers_in_browser(tmp_path)
    assert out["factors"] == [
        {"name": "budget", "points": 30, "detail": "budget >= $25k"},
        {"name": "intent", "points": 15, "detail": "buying intent"},
    ]
    assert out["factorsEmpty"] == []
    # Malformed entries are dropped/coerced, never fabricated into fake factors.
    assert out["factorsMalformed"] == [{"name": "x", "points": 0, "detail": ""}]
