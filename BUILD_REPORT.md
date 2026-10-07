# Build Report

**Project:** AI Lead Qualification & CRM Automation (FastAPI + PostgreSQL + Ollama + n8n)
**Status:** Complete — implementation committed as `c1c0f3a Initial project implementation`;
this session finished the outstanding end-to-end verification and this report.

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

- Schema bootstrap uses `create_all` (`AUTO_CREATE_SCHEMA=true`); production should use
  `alembic upgrade head` + `AUTO_CREATE_SCHEMA=false`.
- Containers reach Ollama only when it listens on the host interface
  (`OLLAMA_HOST=0.0.0.0`); otherwise the deterministic rules fallback applies.
- Notifications are fire-and-forget (no delivery persistence/retry queue).
