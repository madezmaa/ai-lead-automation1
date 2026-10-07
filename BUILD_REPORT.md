# Build Report

**Project:** AI Lead Qualification & CRM Automation (FastAPI + PostgreSQL + Ollama + n8n)
**Status:** MVP complete. Core implementation in `c1c0f3a`, E2E verification + this report in
`1fc7742`, production-safe schema default in `11b538b`, recommended action / CRM record /
notification log in the final MVP commit (see the last section below for its verification).

## Work completed in this session

The application code, tests, migrations, Docker/n8n assets and README were already
finished and committed. The only unfinished items were:

1. **End-to-end verification against a live server** (the scratch script
   `%TEMP%\opencode\e2e_check.ps1` had never produced a clean run).
2. **`BUILD_REPORT.md`** (referenced by the README) — did not exist yet.

### Fixes applied (test script only — no application code changed)

| Problem | Fix |
|---|---|
| `curl --noproxy <url>` consumed the URL → `no URL specified`, server health probe never succeeded | Pass an explicit value: `--noproxy "*" <url>` |
| PowerShell 5.1 native argument quoting mangled inline `-d '{"key": ...}'` bodies → every inline request arrived as broken JSON (`422 json_invalid`) | Write payloads to files (`payload_qualify.json`, `payload_failed.json`, …) and send them with `-d "@file.json"` (same technique the working create-lead call already used) |
| Re-running against the previous run's SQLite file made create return 409 and left `lead.id` empty (cascading 404s) | Delete the throwaway `e2e_check.db` before each run |

All failures found were in the verification script; every application endpoint behaved
correctly once the requests were well-formed.

## Verification results

### Static checks

```
ruff check .        → All checks passed!
ruff format --check → 33 files already formatted
docker compose config -q → ok (compose file valid)
n8n/workflow.json   → parses as valid JSON
```

### Automated tests

```
pytest -m "not ollama" → 132 passed, 1 deselected in 8.32s
pytest -m ollama       → 1 passed, 132 deselected in 30.95s   (live Ollama, llama3.2:3b)
```

133 tests total, all passing (SQLite default; the PostgreSQL variant of the same suite
runs in CI against a `postgres:16-alpine` service).

### End-to-end run (live uvicorn + live Ollama)

Server: `uvicorn app.main:app` on `127.0.0.1:8000`, throwaway SQLite DB,
Ollama `llama3.2:3b` at `localhost:11434`. All 12 checks passed:

| # | Check | Result |
|---|---|---|
| 1 | `GET /health` | `200` — `status=ok`, `database=up`, Ollama reachable, `model_in_use=true` |
| 2 | `POST /api/v1/leads` | `201` — email normalized to `alex.johnson@acme.io`, country `United States/US`, phone `+14155552671` |
| 3 | Duplicate create | `409` `code=duplicate_lead` with existing lead id |
| 4 | Invalid email | `422` validation error |
| 5 | `POST /leads/{id}/qualify` (real Ollama) | `200` in 31s — `decision=qualified`, `score=95`, `ai_used=True`, `fallback_used=False`, `model=llama3.2:3b`, AI reason returned |
| 6 | `GET /leads/{id}` after qualify | `200` — `status=qualified` |
| 7 | Qualification history | `200` — `total=1`, `new → qualified` audit row |
| 8 | Illegal transition (`qualified → failed`) | `409` `code=invalid_transition` with allowed list |
| 9 | Archive transition | `200` — `status=archived` |
| 10 | `GET /leads?status=archived` | `200` — `total=1` |
| 11 | Unknown lead id | `404` `code=lead_not_found` |
| 12 | `GET /openapi.json` | `200` — all 7 documented paths present |

Server log showed a clean startup and the expected Ollama calls
(`GET /api/tags 200`, `POST /api/chat 200`). Temporary artifacts and the test server
were cleaned up afterwards (port 8000 released).

## Not run in this session

- **Docker image build / compose stack** — Docker daemon not available locally; the
  `Dockerfile`, `docker-compose.yml` (validated with `docker compose config -q`) and the
  PostgreSQL test job are covered by CI (`.github/workflows/ci.yml`).
- **HTTP smoke scripts** (`scripts/smoke_test.ps1|sh`) — superseded for this session by
  the e2e run above, which exercises the same endpoints plus live-AI qualification.

## Known limitations (unchanged)

- Containers reach Ollama only when it listens on the host interface
  (`OLLAMA_HOST=0.0.0.0`); otherwise the deterministic rules fallback applies.

## Production-safe schema management (follow-up change)

`AUTO_CREATE_SCHEMA` now defaults to **`false`** in code (`app/config.py`), so a
production deployment never auto-creates tables and must run `alembic upgrade head`.
Local development and tests are unchanged:

- `.env.example` / `.env` still set `AUTO_CREATE_SCHEMA=true` (dev convenience).
- `docker-compose.yml` still passes `${AUTO_CREATE_SCHEMA:-true}` for the local stack.
- `tests/conftest.py` already sets `auto_create_schema: True` explicitly.
- README config table, Alembic section and limitations updated to match.

Verification:

```
Settings(_env_file=None).auto_create_schema → False
AUTO_CREATE_SCHEMA=true env override        → True
pytest tests/test_api.py                    → 21 passed in 4.83s (startup/lifespan paths)
ruff check app/config.py                    → All checks passed!
ruff format --check app/config.py README.md → 2 files already formatted
```

## MVP completion: recommended action, CRM record, notification log

Remaining gaps against the MVP flow (recommended action → CRM-ready record →
notification/action record with failure visibility) were closed without touching the
scoring authority, the state machine or the n8n workflow.

### Changes

| Area | Change |
|---|---|
| Recommended action | `app/domain.py`: `action_for_decision()` maps the final decision to `sales_follow_up` / `add_to_nurture` / `disqualify` (fallback `manual_review`). Derived only from the rules-authoritative decision — the LLM cannot influence it. Persisted in `qualification_results.recommended_action` and returned by the qualify/history endpoints and the webhook payload. |
| CRM integration boundary | New `app/crm.py` (`build_crm_record`) + `GET /api/v1/leads/{id}/crm-record`: structured contact/company/qualification/action record. 409 `not_qualified` before the first qualification, 404 for unknown leads. No external CRM is invented or contacted. |
| Notification reliability | New `notification_logs` table: every qualification writes one row — `delivered`, `failed` (with the error reason from `send_webhook(on_error=...)`), or `skipped` when `NOTIFY_WEBHOOK_URL` is unset. Readable via `GET /api/v1/leads/{id}/notifications`. No queue/infrastructure added. |
| Migration | New Alembic revision `c4a91f7b2e10` (adds `recommended_action` with backfill for existing rows, creates `notification_logs`); downgrade included. |
| Docker | `Dockerfile` now also copies `alembic.ini` + `migrations/`, so `docker compose run --rm api alembic upgrade head` works in the production (`AUTO_CREATE_SCHEMA=false`) configuration. CMD unchanged. |
| README | API table, features, layout, test count and known limitations updated to match reality. |

The n8n workflow was **not** modified: its three HTTP nodes call endpoints that exist
(`POST /leads`, `POST /leads/{id}/qualify`, `POST /leads/{id}/follow-up-draft`), its
expressions (`$json.body`, `$json.id`, `$json.decision`, `$json.lead_id`) match actual
response shapes, and its connection graph is complete.

### Verification performed (this change)

```
pytest tests/test_crm_actions.py tests/test_notifications.py tests/test_api.py
                                                   → 40 passed in 6.57s
pytest (full suite, once after the change)        → 146 passed in 40.30s (133 → 146)
ruff check .                                       → All checks passed!
ruff format --check .                              → 37 files already formatted

alembic upgrade head (fresh SQLite)                → both revisions applied
  tables: alembic_version, leads, qualification_results,
          lead_status_events, idempotency_keys, notification_logs
  qualification_results includes recommended_action
alembic downgrade -1 && alembic upgrade head       → reversible
alembic check                                      → "No new upgrade operations detected"
                                                     (models ↔ migrations in sync)
```

Live E2E (uvicorn on `127.0.0.1:8011`, throwaway SQLite, real Ollama reachable):

```
create:       201, demo@acme.io
qualify:      200, decision=qualified score=94 action=sales_follow_up
crm-record:   200, recommended_action=sales_follow_up, contact normalized
notifications:200, total=1, status=skipped (no NOTIFY_WEBHOOK_URL), event=lead.qualified
crm before qualify: 409 code=not_qualified
```

### Not verified / outside this environment

- **Docker image build & compose stack**: Docker daemon unavailable on this machine.
  Validated statically (`docker compose config -q` passes, Dockerfile paths correct);
  image/stack build runs in CI.
- **n8n execution**: workflow JSON parses and every API call it makes was verified
  individually over HTTP, but the workflow itself was never executed in a live n8n
  instance here.
- **Real webhook delivery**: delivery/failure paths are covered by tests with mock
  receivers; no third-party endpoint was contacted.
- **PostgreSQL test suite**: run in CI (`postgres:16-alpine`); local runs used SQLite.

### Final state

- 146 tests pass, Ruff clean, formatting clean, migrations in sync with models,
  README/BUILD_REPORT match the implementation. Working tree committed and pushed.
