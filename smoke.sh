#!/usr/bin/env bash
# End-to-end smoke test against a running gateway (./start.sh in another terminal).
#   ./smoke.sh <ck_proxy_key> [model]
# Verifies: healthz -> auth -> /v1/models -> a real /v1/chat/completions through Copilot.
set -euo pipefail
BASE=${CRISPY_COPILOT_BASE:-http://127.0.0.1:8812}
KEY=${1:-}
[ -n "$KEY" ] || { echo "usage: ./smoke.sh <ck_proxy_key> [model]"; exit 1; }
AUTH="Authorization: Bearer $KEY"

echo "== healthz (no auth) =="
curl -s "$BASE/healthz"; echo

echo "== /v1/models =="
MODELS=$(curl -s -H "$AUTH" "$BASE/v1/models")
echo "$MODELS" | python3 -c 'import json,sys; d=json.load(sys.stdin); ids=[m["id"] for m in d.get("data",[])]; print(f"{len(ids)} models:", ", ".join(ids[:12]))'
MODEL=${2:-$(echo "$MODELS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])')}
echo "using model: $MODEL"

echo "== /v1/chat/completions (non-stream) =="
curl -s -H "$AUTH" -H "Content-Type: application/json" "$BASE/v1/chat/completions" \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: GATEWAY OK\"}],\"max_tokens\":20}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("reply:", d["choices"][0]["message"]["content"]); print("usage:", d.get("usage")); print("copilot_usage:", d.get("copilot_usage"))'

echo "== done. Check the UI 'Copilot usage' card for the recorded credits. =="
