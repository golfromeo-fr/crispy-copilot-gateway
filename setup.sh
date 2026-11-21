#!/usr/bin/env bash
# One-time bootstrap: install, (optional) client-ID override, first proxy key.
# The GitHub connection itself happens in the management UI ("Connect via browser").
set -euo pipefail
cd "$(dirname "$0")"

# 1. venv + editable install
if [ ! -x .venv/bin/crispy-copilot ] ; then
  echo "[1/3] Creating venv and installing the gateway..."
  python3 -m venv .venv
  .venv/bin/pip install -q -e .
else
  echo "[1/3] venv ready."
fi

# 2. client ID: opencode's is baked in by default; only prompt for an override
[ -f .env ] && set -a && . ./.env && set +a
if [ -z "${CRISPY_COPILOT_CLIENT_ID:-}" ]; then
  echo "[2/3] OAuth client ID: using opencode's registered Copilot client (default)."
  read -rp "Paste your own client ID to override, or press Enter to accept: " OVERRIDE
  OVERRIDE=$(echo "$OVERRIDE" | tr -d '"'"'"' ')
  if [ -n "$OVERRIDE" ]; then
    if [ -f .env ]; then echo "CRISPY_COPILOT_CLIENT_ID=$OVERRIDE" >> .env
    else echo "CRISPY_COPILOT_CLIENT_ID=$OVERRIDE" > .env; fi
    echo "[2/3] Override saved to .env."
  fi
else
  echo "[2/3] Client ID override already set (${CRISPY_COPILOT_CLIENT_ID:0:10}...)."
fi

# 3. first proxy key (skipped when any exist)
if [ -z "$(.venv/bin/crispy-copilot keys)" ]; then
  echo "[3/3] Creating first proxy key 'starter' (shown once - copy it):"
  .venv/bin/crispy-copilot key starter
else
  echo "[3/3] Proxy keys already exist (./start.sh keys to list)."
fi

echo
echo "Bootstrap complete. Start the gateway:   ./start.sh"
echo "Then open the UI:  http://127.0.0.1:8812/ui"
echo "UI login: any username + one of your ck_ keys as the password."
echo "Connect GitHub inside the UI: 'Connect via browser' (device-flow code)."
