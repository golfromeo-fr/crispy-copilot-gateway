#!/usr/bin/env bash
# One-time bootstrap: install, OAuth client ID, GitHub login, first proxy key.
# Re-run any time; each step is skipped when already done.
set -euo pipefail
cd "$(dirname "$0")"

# 1. venv + editable install
if [ ! -x .venv/bin/crispy-copilot ]; then
  echo "[1/4] Creating venv and installing the gateway..."
  python3 -m venv .venv
  .venv/bin/pip install -q -e .
else
  echo "[1/4] venv ready."
fi

# 2. OAuth client ID (device flow needs no client secret)
[ -f .env ] && set -a && . ./.env && set +a
if [ -z "${CRISPY_COPILOT_CLIENT_ID:-}" ]; then
  echo "[2/4] GitHub OAuth app required. Create one:"
  echo "      https://github.com/settings/developers -> New OAuth App"
  echo "      (any name, homepage = repo URL, callback = http://localhost)"
  read -rp "Paste the client ID: " CLIENT_ID
  CLIENT_ID=$(echo "$CLIENT_ID" | tr -d '"'"'"' ')
  [ -n "$CLIENT_ID" ] || { echo "No client ID given - aborting."; exit 1; }
  if [ -f .env ]; then echo "CRISPY_COPILOT_CLIENT_ID=$CLIENT_ID" >> .env
  else echo "CRISPY_COPILOT_CLIENT_ID=$CLIENT_ID" > .env; fi
  export CRISPY_COPILOT_CLIENT_ID="$CLIENT_ID"
  echo "[2/4] Client ID saved to .env."
else
  echo "[2/4] Client ID already set (${CRISPY_COPILOT_CLIENT_ID:0:10}...)."
fi

# 3. GitHub connection via device flow (skipped when one exists)
DATA_DIR=${CRISPY_COPILOT_DATA_DIR:-$HOME/.local/share/crispy-copilot-gateway}
if [ -s "$DATA_DIR/connections.json" ]; then
  echo "[3/4] GitHub connection already exists."
else
  echo "[3/4] Connecting GitHub - a browser window will open..."
  .venv/bin/crispy-copilot login github
fi

# 4. first proxy key (skipped when any exist)
if [ -z "$(.venv/bin/crispy-copilot keys)" ]; then
  echo "[4/4] Creating first proxy key 'starter' (shown once - copy it):"
  .venv/bin/crispy-copilot key starter
else
  echo "[4/4] Proxy keys already exist (./start.sh keys to list)."
fi

echo
echo "Bootstrap complete. Start the gateway:   ./start.sh"
echo "Then open the UI:  http://127.0.0.1:8812/ui"
echo "UI login: any username + one of your ck_ keys as the password."
