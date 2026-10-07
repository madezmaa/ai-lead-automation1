#!/usr/bin/env bash
# Smoke test: boots the API (if needed) and verifies the full HTTP flow.
# Usage: ./scripts/smoke_test.sh [base_url]
set -u
BASE_URL="${1:-http://127.0.0.1:8000}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FAILED=0
STARTED_SERVER=0

check() {
  local name="$1" ok="$2" detail="${3:-}"
  if [ "$ok" = "1" ]; then echo "PASS  $name"
  else echo "FAIL  $name  $detail"; FAILED=$((FAILED + 1)); fi
}

api() {
  local method="$1" path="$2" body="${3:-}" timeout="${4:-200}"
  local args=(--noproxy '*' -sS -m "$timeout" -X "$method" -o "$WORK/body" -w '%{http_code}' "$BASE_URL$path")
  [ -f "$WORK/body" ] && rm -f "$WORK/body"
  if [ -n "$body" ]; then
    printf '%s' "$body" > "$WORK/req.json"
    args+=(-H 'Content-Type: application/json' --data-binary "@$WORK/req.json")
  fi
  local code
  code=$(curl "${args[@]}" 2>/dev/null) || code=000
  [ -n "$code" ] || code=000
  CODE="$code"
  BODY=""
  [ -f "$WORK/body" ] && BODY="$(cat "$WORK/body")"
}

JSON_PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$JSON_PYTHON" ] || JSON_PYTHON="$PYTHON"

json_field() { # json_field <json> <python-expr on obj 'o'>
  "$JSON_PYTHON" -c "import json,sys
try:
    o=json.loads(sys.argv[1]); print(eval(sys.argv[2]))
except Exception: print('')" "$1" "$2"
}

# --- 0. start server if not already up -------------------------------------
api GET /health "" 5
if [ "$CODE" != "200" ]; then
  if [ -x "$PYTHON" ]; then
    (cd "$REPO" && "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
      >"$WORK/server.log" 2>&1 & echo $! > "$WORK/server.pid")
    STARTED_SERVER=1
    for _ in $(seq 1 40); do
      sleep 1
      api GET /health "" 5
      [ "$CODE" = "200" ] && break
    done
  fi
fi
[ "$CODE" = "200" ] && ok=1 || ok=0
check "server up (GET /health -> 200)" "$ok" "(got $CODE)"
H_DB=$(json_field "$BODY" "o.get('database')")
[ "$H_DB" = "up" ] && ok=1 || ok=0
check "health reports database up" "$ok" "$BODY"

# --- 1. create lead ---------------------------------------------------------
STAMP=$(date +%s%3N)
EMAIL="smoke.$STAMP@example.com"
LEAD_PAYLOAD=$(cat <<JSON
{"email":"$EMAIL","first_name":"Sara","last_name":"Doe","company":"SmokeCo",
 "job_title":"CTO","phone":"+1 415 555 0100","website":"smokeco.io",
 "industry":"saas","country":"United States","source":"website",
 "message":"We want a demo and pricing for our team.","budget":50000,"company_size":120}
JSON
)
api POST /api/v1/leads "$LEAD_PAYLOAD"
[ "$CODE" = "201" ] && ok=1 || ok=0
check "create lead -> 201" "$ok" "(got $CODE)"
LEAD_ID=$(json_field "$BODY" "o.get('id')")
[ -n "$LEAD_ID" ] && ok=1 || ok=0
check "created lead has id" "$ok" "$BODY"

# --- 2. duplicate -----------------------------------------------------------
api POST /api/v1/leads "$LEAD_PAYLOAD"
[ "$CODE" = "409" ] && ok=1 || ok=0
check "duplicate email -> 409" "$ok" "(got $CODE) $BODY"

# --- 3. validation ----------------------------------------------------------
api POST /api/v1/leads '{"first_name":"NoEmail"}'
[ "$CODE" = "422" ] && ok=1 || ok=0
check "invalid payload -> 422" "$ok" "(got $CODE)"

# --- 4. idempotency (fresh email) ------------------------------------------
IDEM_PAYLOAD="{\"email\":\"idem.$STAMP@example.com\",\"first_name\":\"Ida\",\"company\":\"IdemCo\",\"source\":\"website\"}"
IDEM1=$(curl --noproxy '*' -sS -m 60 -X POST -H 'Content-Type: application/json' \
  -H "Idempotency-Key: smoke-key-$STAMP" --data-binary "$IDEM_PAYLOAD" \
  -o "$WORK/b1" -w '%{http_code}' "$BASE_URL/api/v1/leads")
IDEM2=$(curl --noproxy '*' -sS -m 60 -X POST -H 'Content-Type: application/json' \
  -H "Idempotency-Key: smoke-key-$STAMP" --data-binary "$IDEM_PAYLOAD" \
  -o "$WORK/b2" -w '%{http_code}' "$BASE_URL/api/v1/leads")
[ "$IDEM1" = "201" ] && ok=1 || ok=0
check "idempotent create -> 201" "$ok" "(got $IDEM1)"
ID1=$(json_field "$(cat "$WORK/b1")" "o.get('id')")
ID2=$(json_field "$(cat "$WORK/b2")" "o.get('id')")
[ "$IDEM2" = "200" ] && [ "$ID1" = "$ID2" ] && ok=1 || ok=0
check "idempotent replay -> 200, same id" "$ok" "(got $IDEM2)"

# --- 5. qualification with real Ollama --------------------------------------
api POST "/api/v1/leads/$LEAD_ID/qualify" '{"use_ai": true}' 300
[ "$CODE" = "200" ] && ok=1 || ok=0
check "qualify -> 200" "$ok" "(got $CODE) $BODY"
DECISION=$(json_field "$BODY" "o.get('decision')")
SCORE=$(json_field "$BODY" "o.get('score')")
AI_USED=$(json_field "$BODY" "o.get('ai_used')")
case "$DECISION" in qualified|nurture|disqualified) ok=1 ;; *) ok=0 ;; esac
check "qualification has decision + score" "$ok" "$BODY"
echo "      decision=$DECISION score=$SCORE ai_used=$AI_USED"

# --- 6. history -------------------------------------------------------------
api GET "/api/v1/leads/$LEAD_ID/qualifications"
TOTAL=$(json_field "$BODY" "o.get('total')")
[ "$CODE" = "200" ] && [ "${TOTAL:-0}" -ge 1 ] && ok=1 || ok=0
check "qualifications history -> 1 result" "$ok" "$BODY"

# --- 7. state machine -------------------------------------------------------
api PATCH "/api/v1/leads/$LEAD_ID/status" '{"status":"archived","reason":"smoke done"}'
[ "$CODE" = "200" ] && ok=1 || ok=0
check "archive lead -> 200" "$ok" "(got $CODE) $BODY"
api PATCH "/api/v1/leads/$LEAD_ID/status" '{"status":"qualifying"}'
[ "$CODE" = "409" ] && ok=1 || ok=0
check "archived -> qualifying rejected -> 409" "$ok" "(got $CODE) $BODY"

# --- 8. follow-up draft -----------------------------------------------------
api POST "/api/v1/leads/$LEAD_ID/follow-up-draft" '{"use_ai": false, "tone": "friendly"}'
SUBJECT=$(json_field "$BODY" "o.get('subject')")
[ "$CODE" = "200" ] && [ -n "$SUBJECT" ] && ok=1 || ok=0
check "follow-up draft (template) -> 200" "$ok" "(got $CODE) $BODY"
api POST "/api/v1/leads/$LEAD_ID/follow-up-draft" '{"use_ai": true}' 300
SUBJECT_AI=$(json_field "$BODY" "o.get('subject')")
[ "$CODE" = "200" ] && [ -n "$SUBJECT_AI" ] && ok=1 || ok=0
check "follow-up draft (AI) -> 200" "$ok" "(got $CODE) $BODY"

# --- 9. list / 404 / openapi ------------------------------------------------
api GET "/api/v1/leads?q=$EMAIL"
TOTAL=$(json_field "$BODY" "o.get('total')")
[ "$CODE" = "200" ] && [ "${TOTAL:-0}" -ge 1 ] && ok=1 || ok=0
check "list/search finds lead" "$ok" "$BODY"
api GET "/api/v1/leads/00000000-0000-0000-0000-000000000000"
[ "$CODE" = "404" ] && ok=1 || ok=0
check "unknown lead -> 404" "$ok" "(got $CODE)"
api GET /openapi.json
[ "$CODE" = "200" ] && ok=1 || ok=0
check "openapi.json -> 200" "$ok" "(got $CODE)"

# --- teardown ---------------------------------------------------------------
if [ "$STARTED_SERVER" = "1" ] && [ -f "$WORK/server.pid" ]; then
  kill "$(cat "$WORK/server.pid")" 2>/dev/null || true
fi

echo
if [ "$FAILED" -eq 0 ]; then echo "SMOKE TEST PASSED"; exit 0; fi
echo "SMOKE TEST FAILED ($FAILED failures)"; exit 1
