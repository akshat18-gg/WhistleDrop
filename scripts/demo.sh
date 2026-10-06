#!/usr/bin/env bash
# Walks through the whole WhistleDrop flow with curl, from an anonymous report
# to a closed case. Start the server first (see the README), then run:
#
#   ./scripts/demo.sh
#
# Set BASE_URL to point it somewhere other than http://localhost:8000.
set -euo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://localhost:8000}"
DEMO_USER="demo"
DEMO_PASSWORD="demo-password-123"
# A random room number, so the moderator can find this run's report with a search
# even if the database already has reports from earlier runs.
ROOM="TP-$RANDOM"

if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi

if command -v jq >/dev/null; then
  pretty() { jq .; }
else
  pretty() { python3 -m json.tool; }
fi

if [ -t 1 ]; then BOLD=$'\033[1m'; RESET=$'\033[0m'; else BOLD=""; RESET=""; fi

step() { printf '\n%s== %s ==%s\n' "$BOLD" "$1" "$RESET"; }

json_field() { python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }

# call METHOD PATH [curl args...]
# Prints the request and the response, and leaves the response in $STATUS and $BODY.
call() {
  local method=$1 path=$2
  shift 2
  echo "> $method $path"
  local previous="" arg
  for arg in "$@"; do
    if [ "$previous" = "-H" ]; then
      case "$arg" in
        Content-Type*) ;;
        Authorization*) echo "> Authorization: Bearer ${TOKEN:0:16}..." ;;
        *) echo "> $arg" ;;
      esac
    elif [ "$previous" = "-d" ]; then
      echo "$arg" | pretty
    fi
    previous=$arg
  done

  local output
  output=$(curl -sS -X "$method" "$BASE_URL$path" -w $'\n%{http_code}' "$@")
  STATUS=${output##*$'\n'}
  BODY=${output%$'\n'*}
  echo "< HTTP $STATUS"
  echo "$BODY" | pretty
}

expect() {
  if [ "$STATUS" != "$1" ]; then
    echo "Expected HTTP $1 but got $STATUS. Stopping here." >&2
    exit 1
  fi
}

if ! curl -s -o /dev/null "$BASE_URL/health"; then
  echo "Can't reach $BASE_URL. Start the server first: uvicorn app.main:app --no-access-log --no-server-header" >&2
  exit 1
fi

step "1. Health check"
call GET /health
expect 200

step "2. Submit a report. No account, no name. The category can be any case."
call POST /api/reports -H "Content-Type: application/json" -d "{
  \"category\": \"security\",
  \"description\": \"The server room door in Tech Park room $ROOM is left unlocked every night after 9pm.\",
  \"evidence_url\": \"https://drive.google.com/file/d/1AbCdEf/view\"
}"
expect 201
CASE_CODE=$(echo "$BODY" | json_field '["case_code"]')

step "3. The reporter checks the status. The code goes in a header, never in the URL."
call GET /api/reports/status -H "X-Case-Code: $CASE_CODE"
expect 200

step "4. Create a demo moderator with the CLI. There's no signup endpoint."
echo "\$ python -m app.cli create-moderator $DEMO_USER --password-stdin"
printf '%s\n' "$DEMO_PASSWORD" | "$PYTHON" -m app.cli create-moderator "$DEMO_USER" --password-stdin \
  || echo "(that's fine, it's left over from an earlier run)"

step "5. The moderator logs in"
call POST /api/auth/login -H "Content-Type: application/json" -d "{\"username\": \"$DEMO_USER\", \"password\": \"$DEMO_PASSWORD\"}"
expect 200
TOKEN=$(echo "$BODY" | json_field '["access_token"]')
AUTH="Authorization: Bearer $TOKEN"

step "6. List SECURITY reports that are still SUBMITTED and mention $ROOM"
call GET "/api/moderator/reports?category=SECURITY&status=SUBMITTED&q=$ROOM" -H "$AUTH"
expect 200
REPORT_ID=$(echo "$BODY" | json_field '["items"][0]["id"]')
echo "The moderator sees a random id, the date and the text. No case code, no time, no name."

step "7. Move it to UNDER_REVIEW with a note the reporter can see"
call PATCH "/api/moderator/reports/$REPORT_ID" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"status": "UNDER_REVIEW", "note": "Thanks for reporting this. We have started looking into it."}'
expect 200

step "8. Add an internal note, only for moderators"
call POST "/api/moderator/reports/$REPORT_ID/updates" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"message": "Asked the security office for last week'"'"'s CCTV logs.", "visible_to_reporter": false}'
expect 201

step "9. The reporter checks again. The visible note shows, the internal one doesn't."
call GET /api/reports/status -H "X-Case-Code: $CASE_CODE"
expect 200

step "10. Try an invalid move: UNDER_REVIEW back to SUBMITTED"
call PATCH "/api/moderator/reports/$REPORT_ID" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"status": "SUBMITTED"}'
expect 409

step "11. Resolve it"
call PATCH "/api/moderator/reports/$REPORT_ID" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"status": "RESOLVED", "note": "A new lock has been fitted and security now checks the room at 9pm."}'
expect 200

step "12. Close the case for good"
call POST "/api/moderator/reports/$REPORT_ID/close" -H "$AUTH"
expect 200

step "13. Try to add a note to the closed case"
call POST "/api/moderator/reports/$REPORT_ID/updates" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"message": "One more thing."}'
expect 409

step "14. The reporter checks one last time"
call GET /api/reports/status -H "X-Case-Code: $CASE_CODE"
expect 200

printf '\n%sDone.%s The reporter followed their case from start to finish without ever saying who they are.\n' "$BOLD" "$RESET"
