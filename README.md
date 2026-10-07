# AI Lead Qualification & CRM Automation

FastAPI service that captures inbound leads, validates/normalizes them, qualifies them with a
**deterministic rules engine + local Ollama AI**, moves them through an explicit state machine,
and integrates with **n8n** for follow-up automation.

```
webhook / API ──► POST /api/v1/leads ──► PostgreSQL
                        │
                        ▼
              POST /api/v1/leads/{id}/qualify
                        │
        ┌───────────────┴────────────────┐
        ▼                                ▼
 deterministic rules          Ollama AI (structured JSON)
        │                         │  (retries, then fallback)
        └───────────┬─────────────┘
                    ▼
        blend / hard-disqualifiers ──► decision: qualified | nurture | disqualified
                    │
                    ▼
        state machine transition + audit rows (lead_status_events,
        qualification_results) + optional outbound webhook notification
```

## Features

- **Lead capture API** – create/list/search/retrieve leads with full input validation and
  normalization (email, phone → E.164-ish, country → ISO-2, website, source, names).
- **Duplicates & idempotency** – email is the dedup key (409), plus an optional
  `Idempotency-Key` header so retried creates replay the first response (200) instead of failing.
- **Deterministic scoring** – transparent 0–100 score from named factors (budget, company size,
  intent keywords, business email, target industry/country, seniority, completeness, source)
  with hard disqualifiers (budget floor, opt-out phrases). Same input ⇒ same output.
- **Ollama AI layer** – `POST /api/chat` with `format: "json"` and strict Pydantic validation.
  Transient failures (timeout/5xx/network) are retried (`OLLAMA_MAX_RETRIES`).
- **Automatic fallback** – any AI failure ⇒ the deterministic rules decide the outcome.
  Hard rule violations (e.g. opt-out) always override the AI.
- **Blending** – final score = `round(rules·(1-w) + ai·w)` with `w = AI_BLEND_WEIGHT` (default 0.6),
  then thresholds (`QUALIFIED_THRESHOLD=70`, `NURTURE_THRESHOLD=40`).
- **Explicit state machine** – every transition goes through `app/state_machine.py`:
  `new → qualifying → qualified|nurture|disqualified|failed`, `→ archived`, `archived → new`.
  Illegal transitions return **409**.
- **Audit trail** – every status change writes `lead_status_events`; every qualification stores
  rules output, AI output, blend flags, model and **recommended action** in
  `qualification_results`.
- **Recommended action** – derived only from the final decision (`sales_follow_up`,
  `add_to_nurture`, `disqualify`, fallback `manual_review`); the LLM can never influence it.
- **CRM-ready record** – `GET /api/v1/leads/{id}/crm-record` returns a clean, structured
  record (contact, company, qualification, recommended action) for a CRM/iPaaS consumer.
  This is the integration boundary: no external CRM is contacted by the API itself.
- **Follow-up drafts** – `POST /api/v1/leads/{id}/follow-up-draft` returns an AI-written
  email (subject + body) with a deterministic template fallback. Drafts are never sent.
- **Notifications** – set `NOTIFY_WEBHOOK_URL` and every completed qualification POSTs a JSON
  payload (`event: lead.<decision>`, including the recommended action) to your endpoint.
  Best-effort, never fails the request, and every attempt (delivered / failed / skipped) is
  recorded in `notification_logs` and readable via
  `GET /api/v1/leads/{id}/notifications`.
- **Security/config** – optional `API_KEY` (`X-API-Key` header, constant-time compare),
  no secrets in the repo, `.env.example` documents every setting, non-root Docker image.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + database + Ollama reachability |
| `POST` | `/api/v1/leads` | Create lead (409 on duplicate email; 201, or 200 on `Idempotency-Key` replay) |
| `GET` | `/api/v1/leads` | List/search (`status`, `source`, `q`, `limit`, `offset`) |
| `GET` | `/api/v1/leads/{id}` | Get one lead |
| `POST` | `/api/v1/leads/{id}/qualify` | Run rules + AI, store result, transition status |
| `GET` | `/api/v1/leads/{id}/qualifications` | Qualification history |
| `GET` | `/api/v1/leads/{id}/crm-record` | CRM-ready record of the latest qualification (409 `not_qualified` before qualifying) |
| `GET` | `/api/v1/leads/{id}/notifications` | Notification delivery log (`delivered` / `failed` / `skipped`) |
| `POST` | `/api/v1/leads/{id}/follow-up-draft` | AI follow-up email draft (template fallback) |
| `PATCH` | `/api/v1/leads/{id}/status` | Manual state transition (409 if illegal) |
| `GET` | `/docs` · `/openapi.json` | Interactive docs |

Errors are uniform:
`{"detail": "...", "code": "duplicate_lead" | "invalid_transition" | "lead_not_found" | "not_qualified"}`.

```bash
# create
curl -s localhost:8000/api/v1/leads -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: form-submit-1' -d '{
    "email":"alex@acme.io","first_name":"Alex","company":"Acme",
    "industry":"saas","country":"United States","message":"Demo + pricing please",
    "budget":20000,"company_size":250,"source":"website"}'

# qualify (rules + Ollama, automatic fallback)
curl -s -X POST localhost:8000/api/v1/leads/<id>/qualify \
  -H 'Content-Type: application/json' -d '{"use_ai": true}'

# follow-up draft
curl -s -X POST localhost:8000/api/v1/leads/<id>/follow-up-draft \
  -H 'Content-Type: application/json' -d '{"use_ai": true, "tone": "friendly"}'
```

## Quickstart (local)

Requires Python ≥ 3.11, Docker (for PostgreSQL) and, for AI, a running
[Ollama](https://ollama.com) with `ollama pull llama3.2:3b`.

```powershell
# 1. dependencies
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt

# 2. configuration
copy .env.example .env          # adjust if needed

# 3. database
docker compose up -d db

# 4. run
.\.venv\Scripts\uvicorn app.main:app --reload
# → http://127.0.0.1:8000/health
```

Linux/macOS: use `.venv/bin/...` instead.

### Using Alembic instead of auto-create

The API bootstraps the schema with `create_all` only when `AUTO_CREATE_SCHEMA=true`
(the dev `.env` / docker-compose default; the code default is `false` so production
deployments never auto-create tables). For migration-managed environments:

```bash
export DATABASE_URL=postgresql+psycopg://lead:lead@localhost:5432/leads
alembic upgrade head          # apply migrations (fresh DB)
alembic stamp head            # existing DB already built by create_all
alembic revision --autogenerate -m "describe change"   # after editing models
alembic check                 # fail if models and migrations drift apart
```

## Docker Compose

```bash
docker compose up -d db api          # PostgreSQL + API on :8000
docker compose --profile n8n up -d   # + n8n on :5678
```

The `api` service points `DATABASE_URL` at the `db` service and Ollama at
`http://host.docker.internal:11434`. On Windows/macOS the Ollama server must accept
non-localhost connections (`OLLAMA_HOST=0.0.0.0`) for the *container* to reach it;
otherwise qualifications still succeed via the deterministic fallback (verified in
`BUILD_REPORT.md`).

## n8n workflow

Import [`n8n/workflow.json`](n8n/workflow.json) in n8n (Profiles → Workflows → Import):

1. **Lead Webhook** receives a lead payload.
2. **Create Lead** (`POST /api/v1/leads`, idempotency key = email).
3. **Qualify Lead** (`POST /api/v1/leads/{id}/qualify`).
4. **Route by Decision** → *Notify Sales* / *Add to Nurture* / *Mark Disqualified*.
5. Qualified leads get an AI **follow-up draft** from the API.

If the API runs elsewhere, edit the `host.docker.internal:8000` base URL in both HTTP nodes.
If `API_KEY` is set, attach a Header Auth credential (`X-API-Key`) to them.

## Configuration

All settings come from environment variables / `.env` (see [`.env.example`](.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://lead:lead@localhost:5432/leads` | SQLAlchemy URL (PostgreSQL/Supabase/SQLite) |
| `AUTO_CREATE_SCHEMA` | `false` (dev `.env`: `true`) | Create tables on startup instead of using Alembic |
| `API_KEY` | *(unset)* | When set, all `/api/v1` endpoints require `X-API-Key` |
| `LOG_LEVEL` | `INFO` | Root logging level |
| `OLLAMA_ENABLED` | `true` | Use the AI layer |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server |
| `OLLAMA_MODEL` | `llama3.2:3b` | Model name |
| `OLLAMA_TIMEOUT_SECONDS` | `120` | Per-request timeout |
| `OLLAMA_MAX_RETRIES` | `2` | Attempts for transient failures |
| `AI_BLEND_WEIGHT` | `0.6` | AI share of the blended score (0..1) |
| `QUALIFIED_THRESHOLD` / `NURTURE_THRESHOLD` | `70` / `40` | Decision thresholds |
| `MIN_BUDGET` | `1000` | Hard budget disqualifier |
| `TARGET_INDUSTRIES` / `TARGET_COUNTRIES` / `FREE_EMAIL_DOMAINS` | see file | JSON arrays |
| `NOTIFY_WEBHOOK_URL` | *(unset)* | Outbound notification webhook after qualification |
| `NOTIFY_TIMEOUT_SECONDS` | `3` | Webhook timeout |

## State machine

```
new ────────► qualifying ──► qualified | nurture | disqualified | failed
 │              ▲   │                    │
 │              │   └────────────────────┘   (re-qualify)
 ▼              │
archived ◄──────┴────────── any of qualified/nurture/disqualified/failed
 │
 └──► new   (restore)
```

`ALLOWED_TRANSITIONS` in [`app/state_machine.py`](app/state_machine.py) is the single source of
truth; anything else returns **409 `invalid_transition`**.

## Tests & checks

```powershell
.\.venv\Scripts\ruff.exe check .          # lint
.\.venv\Scripts\ruff.exe format .         # format
.\.venv\Scripts\python.exe -m pytest      # 146 tests (SQLite by default)

# same suite against real PostgreSQL:
$env:TEST_DATABASE_URL='postgresql+psycopg://lead:lead@localhost:5432/leads_test'
.\.venv\Scripts\python.exe -m pytest
```

- Live Ollama tests are marked `ollama` (`pytest -m "not ollama"` to skip).
- End-to-end HTTP smoke tests (require the server + Ollama for the AI assertions):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\smoke_test.ps1
```
```bash
./scripts/smoke_test.sh
```

**CI**: [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs Ruff, the SQLite suite and
the PostgreSQL suite (with a `postgres:16-alpine` service) on every push/PR.

## Project layout

```
app/
  main.py            FastAPI factory, exception handlers, logging
  config.py          Settings (env/.env)
  models.py          SQLAlchemy models: leads, qualification_results,
                     lead_status_events, idempotency_keys, notification_logs
  schemas.py         Pydantic request/response models (validation + normalization hooks)
  normalization.py   email/phone/country/website/source normalizers
  rules.py           deterministic scoring engine
  domain.py          LeadProfile, rules/AI results, decide() blending, recommended actions
  ai.py              Ollama client: structured qualification, retries, follow-up drafts
  state_machine.py   statuses + ALLOWED_TRANSITIONS
  service.py         transactions, create/qualify/transition orchestration
  notifications.py   outbound webhook notifications + delivery payload
  crm.py             CRM integration boundary (CRM-ready record builder)
  security.py        API-key dependency
  routers/           leads + health endpoints
migrations/          Alembic migrations
tests/               146 unit/API/live tests
scripts/             smoke_test.ps1 / smoke_test.sh
n8n/workflow.json    importable n8n workflow
Dockerfile, docker-compose.yml, .github/workflows/ci.yml
```

## Known limitations

- Schema bootstrap uses `create_all` only when explicitly enabled (dev convenience);
  production deployments run `alembic upgrade head` with `AUTO_CREATE_SCHEMA=false`
  (the default). The Docker image ships `alembic.ini` + `migrations/`, so
  `docker compose run --rm api alembic upgrade head` works from the container.
- Docker containers can reach Ollama only when Ollama listens on the host interface
  (`OLLAMA_HOST=0.0.0.0`); otherwise the deterministic fallback covers qualification.
- Notifications have no retry queue: every attempt is recorded once
  (`delivered` / `failed` / `skipped` in `notification_logs`) but failures are not retried.
- No external CRM is contacted. `GET /api/v1/leads/{id}/crm-record` produces the
  CRM-ready record; wiring it to a real CRM happens downstream (e.g. in n8n).
- Follow-up drafts are generated only — the system never sends messages to customers.
- Not executed in this environment: the n8n workflow (JSON validated and its API calls
  verified individually) and the Docker image build (Docker daemon unavailable; compose
  config validated statically, image/stack exercised in CI).
