#!/usr/bin/env bash
# End-to-end smoke test against a running gateway (./start.sh in another terminal).
#   ./smoke.sh <ck_proxy_key> [model]
# Verifies: healthz -> auth -> /v1/models -> a real /v1/chat/completions -> credits recorded.
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

echo "== credits recorded in usage.db =="
DB="${CRISPY_COPILOT_DATA_DIR:-$HOME/.local/share/crispy-copilot-gateway}/usage.db"
python3 - "$DB" "$MODEL" <<'PY'
import sqlite3, sys
db, model = sys.argv[1], sys.argv[2]
try:
    c = sqlite3.connect(db)
    row = c.execute("SELECT input_tokens,output_tokens,nano_aiu FROM usage "
                    "WHERE model=? ORDER BY id DESC LIMIT 1", (model,)).fetchone()
    c.close()
except Exception as e:
    print("FAIL: cannot read usage.db:", e); sys.exit(1)
if not row:
    print("FAIL: no usage row recorded for", model); sys.exit(1)
print("recorded:", {"input_tokens": row[0], "output_tokens": row[1], "nano_aiu": row[2]})
if not row[2]:
    print("WARN: nano_aiu is 0 (this request was not billed)")
PY
echo "== done. =="
