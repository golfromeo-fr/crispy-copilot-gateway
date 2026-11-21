#!/usr/bin/env bash
# crispy-copilot-gateway starter.
#   ./start.sh              -> serve API + management UI on one port
#   ./start.sh login work   -> any crispy-copilot subcommand, passthrough
set -euo pipefail
cd "$(dirname "$0")"

# Optional overrides live in .env next to this script (git-ignored):
#   CRISPY_COPILOT_CLIENT_ID=...   # once, from github.com/settings/developers
#   CRISPY_COPILOT_DATA_DIR=...
#   CRISPY_COPILOT_HOST=...  CRISPY_COPILOT_PORT=...
[ -f .env ] && set -a && . ./.env && set +a

if [ ! -x .venv/bin/crispy-copilot ]; then
  echo "First run: creating venv and installing the gateway..."
  python3 -m venv .venv
  .venv/bin/pip install -q -e .
fi

# Subcommand passthrough (login, key, keys, revoke, ...).
if [ "$#" -gt 0 ]; then
  exec .venv/bin/crispy-copilot "$@"
fi

HOST=${CRISPY_COPILOT_HOST:-127.0.0.1}
PORT=${CRISPY_COPILOT_PORT:-8812}
echo "API: http://$HOST:$PORT/v1"
echo "UI:  http://$HOST:$PORT/ui  (login: any username + one ck_ key as password)"
exec .venv/bin/crispy-copilot serve --host "$HOST" --port "$PORT"
