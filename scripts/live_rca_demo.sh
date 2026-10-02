#!/usr/bin/env bash
set -euo pipefail

: "${ANTHROPIC_API_KEY:?Set ANTHROPIC_API_KEY before running this demo}"
: "${JWT_SECRET_KEY:?Set JWT_SECRET_KEY (for example: openssl rand -hex 32)}"

export RCA_MODE=live
export LLM_PROVIDER=anthropic
export LLM_API_KEY="$ANTHROPIC_API_KEY"

cleanup() {
  if [[ "${KEEP_DEMO_STACK:-0}" != "1" ]]; then
    docker compose down --remove-orphans >/dev/null
  fi
}
trap cleanup EXIT

docker compose up --build -d
echo "Waiting for the incident API..."
for _ in $(seq 1 60); do
  curl --fail --silent http://localhost:8002/health >/dev/null && break
  sleep 2
done

token="$(curl --fail --silent -X POST http://localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"admin","password":"admin123"}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
echo "Generating controlled latency traffic..."
python3 scripts/generate_traffic.py --scenario latency --duration 45 --rate 5 >/tmp/sentinelops-live-traffic.log 2>&1 &
traffic_pid=$!

incident_id=""
for _ in $(seq 1 45); do
  incident_id="$(curl --fail --silent \
    -H "Authorization: Bearer $token" \
    'http://localhost:8002/incidents?limit=1' |
    python3 -c 'import json,sys; rows=json.load(sys.stdin); print(rows[0]["id"] if rows else "")')" || true
  [[ -n "$incident_id" ]] && break
  sleep 2
done
wait "$traffic_pid" || true

if [[ -z "$incident_id" ]]; then
  echo "No incident was created; traffic log:" >&2
  cat /tmp/sentinelops-live-traffic.log >&2
  exit 1
fi

curl --fail --silent -X POST http://localhost:8004/investigations \
  -H "Authorization: Bearer $token" -H 'content-type: application/json' \
  -d "{\"incident_id\":\"$incident_id\"}" >/dev/null

echo "Live RCA report for incident $incident_id:"
curl --fail --silent -H "Authorization: Bearer $token" \
  "http://localhost:8004/incidents/$incident_id/investigation" | python3 -m json.tool
