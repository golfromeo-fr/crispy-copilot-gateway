# Handover — crispy-copilot-gateway

Context doc for a fresh chat/session working on this gateway or its ZCode
wrapper. Written 2026-11-23; facts pinned to commit SHAs — re-verify anything
that looks stale.

## What this repo is

**crispy-copilot-gateway**: local API gateway for GitHub Copilot. Serves
OpenAI (`/v1/chat/completions`, `/v1/responses`), Anthropic (`/v1/messages`,
`/v1/messages/count_tokens`), and `/v1/models` on `127.0.0.1:8812` by default,
backed by your GitHub account's Copilot entitlement (OAuth device flow, token
stored 0600). Credits metering (`copilot_usage.total_naiu`) into SQLite.
FastAPI + NiceGUI management UI at `/ui` (login: any username + a `ck_` key).

Authoritative docs: [README.md](../README.md). Tests: `tests/` (pytest) +
`smoke.sh` (live end-to-end). Python ≥ 3.12, deps in `pyproject.toml`
(fastapi, uvicorn, httpx, nicegui, typer), hatchling build.

## File map (src/crispy_copilot_gateway/)

| File | Role |
|---|---|
| `proxy.py` | FastAPI app: auth middleware (Bearer `ck_` keys / Basic for UI), `/healthz` (only unauth endpoint), route forwarding, SSE passthrough |
| `adapt.py`, `adapt_stream.py` | chat/Responses/Anthropic shape adaptation for models that only speak `/responses` |
| `routing.py` | per-connection `supported_endpoints` cache (TTL 600s) → chooses chat vs responses upstream |
| `copilot.py` | upstream Copilot HTTP client (client fingerprint, endpoint cache) |
| `connections.py` | device flow (RFC 8628), connection store, active-account logic (selected label or first entry) |
| `keys.py` | proxy keys: SHA-256-hashed `ck_` keys, `last_used` throttle, account pins (P4) |
| `config.py` | data-dir layout, 0600 atomic writes (`write_private`) |
| `usage.py` | SQLite metering (`usage.db`): `totals()`, `recent()`, SSE/usage parsing, `nano_aiu/1e11 = usd` |
| `cli.py` | `serve`, `login <label>` (device flow), `key <name>`, `keys`, `revoke <name>` |
| `ui*.py` | NiceGUI management UI (accounts, models card, keys, usage) |

## State (store) layout

Default store: `~/.local/share/crispy-copilot-gateway/` (override:
`CRISPY_COPILOT_DATA_DIR`): `connections.json` (label → github_token, 0600),
`active.json`, `api-keys.json` (hashes only), `usage.db`, `storage_secret.txt`.
`.env` next to the checkout overrides host/port/data dir (`CRISPY_COPILOT_*`).

**Store modes in the wild** (important when debugging):
1. standalone checkout (this repo, `./start.sh`) → default shared store
2. ZCode wrapper plugin → own store at `<plugin data dir>/store/`
   (`~/.zcode/plugins/copilot-gateway/store` on Linux, equivalent on Windows) —
   isolated on purpose, so wrapper installs never touch mode-1 data.

## The ZCode wrapper plugin (separate repo)

Wrapper lives in **golfromeo-zcode-toolbox** (github: golfromeo-fr/zcode-toolbox),
plugin `plugins/copilot-gateway` (0.1.8 @ 63dcf55, 2026-11-23). It is NOT a copy
of the gateway: on first `gateway_start` it **clones this repo** from GitHub into
the plugin data dir, creates the venv, pip-installs, and serves `serve` with the
isolated store. `gateway_update` = fetch + `reset --hard origin/main` + pip +
restart (like a manual `git pull`). Anonymous hermetic git (empty-file config;
no credentials). All child processes `windowsHide`.

12 MCP tools (`mcp__plugin_copilot-gateway_gateway__gateway_*`): status /
start / stop / update / login / login_check / keys / key_create / key_revoke /
key_pin / use / credits. `gateway_use` returns `{baseUrl, apiKey, models}` where
`models` are **full catalog entries** (vision + max images, reasoning_effort
levels array, adaptive_thinking, thinking budgets, tool_calls, structured
outputs, streaming, context/output/prompt limits, per-M prices) — flattened
from the upstream Copilot catalog by the plugin's `flattenModel`.

## Windows gotchas (all fixed, keep them fixed)

- view bridge global is `window.zcode` (same surface as `window.host`:
  ready/callTool/listResources/subscribeResource/onResourceUpdated) — plugin
  views duck-type both. Verified via context-lens (binds `window.zcode`).
- git not on the GUI process PATH (stale env) → plugin probes standard
  install locations; `git_path` userConfig override.
- `os.devNull` breaks git-for-windows (`//./nul` treated as a remote) →
  hermetic git config must be a real empty file.
- every child spawn needs `windowsHide: true` or a blank console flashes.

## Coordination rules

- Service behavior → change HERE, push, then `gateway_update` on the machine
  (or restart via plugin/standalone). The plugin never patches gateway code.
- Wrapper/plugin behavior → change in zcode-toolbox `plugins/copilot-gateway`,
  bump the manifest + marketplace versions together, push; instances update via
  the marketplace. Keep vendored copies OUT (none today; keep it that way).
- Both repos: small verified commits; the toolbox has release-plugin.sh /
  sync-plugin.sh for releases.

## Quick verify recipes

- health: `curl -s http://127.0.0.1:8812/healthz` → `{"ok":true}`
- live smoke: `./smoke.sh` (this repo) — end-to-end against real Copilot
- unit: `.venv/bin/python -m pytest tests/`
- catalog peek (standalone): create a temp key (`crispy-copilot key tmp`),
  `curl -H "Authorization: Bearer ck_..." /v1/models`, revoke it — entries
  carry `capabilities.supports` / `limits` / `billing.token_prices`.
