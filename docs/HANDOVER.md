# Handover — crispy-copilot-gateway + ZCode wrapper

Complete context for a fresh chat/session touching either the gateway (this
repo) or its ZCode wrapper plugin. Written 2026-11-23; facts pinned to commit
SHAs and versions — re-verify anything that looks stale.

**Repos**
- Gateway (this repo): `golfromeo-fr/crispy-copilot-gateway` @ `046e754`
- Wrapper plugin: `golfromeo-fr/zcode-toolbox`, `plugins/copilot-gateway`
  v0.2.0 @ `2a2b2dd` (marketplace: `golfromeo-zcode-toolbox`; sibling plugins
  context-lens 1.6.2, token-speed 1.2.1)

## Part A — the gateway (this repo)

Local API gateway for GitHub Copilot. Serves on `127.0.0.1:8812` (default):

| Path | Speaks | Auth |
|---|---|---|
| `/healthz` | `{"ok":true}` | none (only unauth endpoint) |
| `/v1/chat/completions`, `/v1/responses` | OpenAI | Bearer `ck_` key |
| `/v1/messages`, `/v1/messages/count_tokens` | Anthropic | Bearer or x-api-key |
| `/v1/models` | Copilot catalog (raw upstream entries) | Bearer `ck_` key |
| `/ui` | NiceGUI management UI | Basic: any username + a `ck_` key |

Backed by a GitHub account's Copilot entitlement via OAuth **device flow**
(RFC 8628; client ID = opencode's `Ov23li8tweQw6odWQebz`, override
`CRISPY_COPILOT_CLIENT_ID`). Entitlement check `/copilot_internal/user`; the
token is stored 0600; a fresh login becomes the active account. Credits
metering: `copilot_usage.total_nano_aiu` → SQLite (`credits = nano_aiu/1e9`,
`usd = nano_aiu/1e11`).

### File map (src/crispy_copilot_gateway/)

| File | Role |
|---|---|
| `proxy.py` | FastAPI app: auth middleware, `/healthz`, forwarding, SSE passthrough; 503 with a clear error when a request has no connection |
| `adapt.py`, `adapt_stream.py` | chat/Responses/Anthropic adaptation for models that only serve `/responses` |
| `routing.py` | per-connection `supported_endpoints` cache (TTL 600s) → chat vs responses |
| `copilot.py` | upstream HTTP client (opencode-compatible fingerprint) |
| `connections.py` | device flow, store, active-account logic (selected label, else first entry) |
| `keys.py` | `ck_` keys (SHA-256 at rest, raw shown once), `last_used` 60s throttle, account pins (P4) |
| `config.py` | data-dir layout, atomic 0600 writes (`write_private`) |
| `usage.py` | SQLite metering: `totals()`, `recent()`, SSE/usage parsing; **stdlib-only** (wrapper reads it with the venv python) |
| `cli.py` | `serve`, `login <label>`, `key <name>`, `keys`, `revoke <name>` |
| `ui*.py` | NiceGUI UI (accounts, models card, keys, usage) |

### State

Default store `~/.local/share/crispy-copilot-gateway/` (`CRISPY_COPILOT_DATA_DIR`
or repo `.env` `CRISPY_COPILOT_HOST/PORT/DATA_DIR` override):
`connections.json` (label → github_token, 0600), `active.json`,
`api-keys.json` (hashes only), `usage.db`, `storage_secret.txt`.

**Store modes** (top debugging question: WHICH store is a given gateway using?)
1. standalone checkout (`./start.sh`) → default shared store
2. ZCode wrapper plugin → `~/.zcode/plugin-data/copilot-gateway/store/`
   (plugin 0.2.0+; `CRISPY_GATEWAY_RUNTIME_DIR` override) — isolated on
   purpose; pre-0.2.0 stores auto-migrate on first status/start

### Concurrency facts (do not "fix" blindly)

Key-store read-modify-write (`keys.py`) is safe inside the running gateway
(single async process, sync handlers can't interleave). It CAN race between
external CLI processes — the wrapper serializes its tool calls for this
reason. Don't add locking unless you first reproduce a real in-gateway race.

### Verify recipes

- `curl -s http://127.0.0.1:8812/healthz`
- unit: `.venv/bin/python -m pytest tests/`; live e2e: `./smoke.sh`
- catalog peek: temp key (`crispy-copilot key tmp`) → `GET /v1/models` →
  revoke. Entries carry `capabilities.supports` (vision, `reasoning_effort`
  as an ARRAY of allowed levels, adaptive_thinking, thinking budgets,
  tool_calls, structured_outputs), `capabilities.limits`,
  `billing.token_prices`, `supported_endpoints`, `policy.state`.
- Windows: the plugin spawns `.venv\Scripts\crispy-copilot.exe` directly;
  `start.sh` is POSIX-only (not used by the wrapper).

## Part B — the wrapper plugin (zcode-toolbox / plugins/copilot-gateway)

Zero-dep Node ESM. Layout: `loader.js` (cache-busting shim) →
`impl/impl.js` (chip + view), `impl/gateway.js` (ALL gateway control:
config, healthz, files, CLI, lifecycle), `impl/mcp.js` (stdio JSON-RPC
server), `view.html`, `skills/setup/SKILL.md`, manifest
`.zcode-plugin/plugin.json` (mcpServers + userConfig + slots).

### What it does

On first `gateway_start`: clones THIS repo from GitHub into
`<runtimeDir>/gateway`, detects python ≥ 3.12, creates the venv, pip-installs
(~20s), spawns `serve` on the configured host:port with `--idle-exit 30`
(default; `idle_exit` userConfig, 0 = keep running). While ZCode runs the chip's
5s /healthz poll keeps the gateway alive, so a forgotten gateway exits gracefully
~30 min after ZCode closes (needs gateway >= 0.1.1 / 1562113). `gateway_update`:
fetch → count behind → `reset --hard origin/main` → pip → restart if running
(manual `git pull`, wrapped). All git access is **anonymous + hermetic**
(empty-file git config, `GIT_TERMINAL_PROMPT=0`, no user/system gitconfig, no
credential helpers — hostile-gitconfig + SSH-tripwire tested). Never touches
an explicit `gateway_path` external checkout; stale non-git managed dir is
wiped + re-cloned. Every child process is spawned with `windowsHide: true`.

**Runtime dir** (default `~/.zcode/plugin-data/copilot-gateway`, override
`CRISPY_GATEWAY_RUNTIME_DIR`) holds: `store/`, `gateway/` (clone+venv),
`serve.log`, `install.log`, `serve.pid`, `gitconfig-empty`, `login-*.log`.
It is OUTSIDE the plugin dir on purpose — plugin updates replace the plugin
dir; before 0.2.0 the store lived inside it and plugin deploys silently wiped
the OAuth account (this actually happened). Deploys must never `rm -rf`
runtime data.

**userConfig** (Plugins page): `gateway_path` (external checkout — never
touched by update), `host`, `port`, `gateway_repo` (fork), `git_path`
(non-standard git location). Passed to the MCP server as `CRISPY_GATEWAY_*`
env. The gateway additionally respects `CRISPY_COPILOT_DATA_DIR`.

### MCP tools (13) — `mcp__plugin_copilot-gateway_gateway__<name>`

status · start · stop · update · login / login_check (device flow:
`gateway_login {label}` returns code+url and opens the system browser;
`login_check` until `connected`) · keys · key_create / key_revoke / key_pin ·
**config** (provider config for an EXISTING key; apiKey = placeholder; reads
the catalog via a throwaway `<key>-catalog-tmp` key, revoked immediately;
stale temp keys cleaned) · **use** (first-time: create key, raw shown ONCE) ·
credits. Tool execution is **serialized** (promise queue) — gateway key ops
are external-process RMW. Data dirs with no connected account return
`HTTP 503` / `models_full: null` — expected, not a bug.

`gateway_use`/`gateway_config` return `provider.models` as **full flattened
catalog entries** (plugin-side `flattenModel`): id, name, vendor, preview,
enabled, endpoints, vision + max_images, reasoning_effort levels array,
adaptive_thinking, thinking budgets, tool_calls, structured_outputs,
streaming, context/output/prompt limits, per-M in/out prices.

### View + chip

Composer chip `gw <latency>ms` / `gw down` (tooltip: accounts★/keys/credits/$);
click opens the side pane. View cards: start/stop button (+ per-state down
hints), Connect GitHub account (login…, click-to-copy code), Accounts,
Pending logins, Proxy keys (pin select + revoke), Copilot credits (totals +
recent), Harness config (**config…** regenerate for existing key ·
**new key…** first-time · **copy** to clipboard), update…. The `updated`
line shows the bound bridge global (`· bridge zcode|host`) for debugging.

### Windows gotchas — ALL fixed; keep them fixed

1. View bridge global is **`window.zcode`** (same surface as `window.host`:
   ready/callTool/listResources/subscribeResource/onResourceUpdated/locale).
   Views must WAIT for injection and duck-type both (probe `ready`+`callTool`).
   token-speed 1.2.1 has the same fix; context-lens always bound `window.zcode`.
2. Git not on the GUI process PATH (stale env) → `resolveGit()` probes
   Program Files, PF(x86), LOCALAPPDATA\Programs\Git, scoop; `git_path` config.
3. `os.devNull` (`\\.\nul`) breaks git-for-windows → it normalizes to
   `//./nul` and treats it as a remote. Hermetic git config must be a real
   empty file (`gitconfig-empty`).
4. Every child spawn needs `windowsHide: true` or blank cmd windows flash.
5. Runtime data must never live under the plugin dir (see 0.2.0 above).

### Wrapper dev workflow (zcode-toolbox)

Bump `plugins/copilot-gateway/.zcode-plugin/plugin.json` version AND
`marketplace.json` entry together; commit + push; instances update via the
marketplace and toggle off/on (loader shim re-imports impl with a cache
buster). `release-plugin.sh` / `sync-plugin.sh` exist for releases.
Verification patterns that caught real bugs: runtime view smoke
(browser-faithful stubs, zcode-only bridge — `node --check` alone is NOT
enough), isolated-port lifecycle tests (:8813-:8825 used so far), temp-key
create/revoke for catalog reads, planted-legacy-store migration test.

## Part C — machine states (as of 2026-11-23)

**Home Linux box** (re-verified 2026-11-23): standalone gateway serving
`:8812` from `~/crispy-copilot-gateway` with the shared store (accounts +
keys + credit history intact — plugin work never touched it; 130 unit tests
pass). The plugin is deployed at `~/.zcode/plugins/copilot-gateway`
(0.2.0-era code: `plugin-data` runtime default + migration present) but NOT
installed in that ZCode instance. The runtime dir
`~/.zcode/plugin-data/copilot-gateway` is **absent**, and no legacy in-plugin
store remains — earlier "install + `zcode` key, no account" state is gone.
First `gateway_start` here = fresh clone + venv + `login` from scratch.

**Windows 11 VM**: full working setup — plugin installed, gateway installed
via the wrapper, account connected (device flow), credits metering observed
(6 req), ZCode wired by pasting the provider JSON into a chat. After
updating to 0.2.0 the migration should preserve the account.

## Part D — open/optional items

Nothing required. Optional polish, either repo:
- gateway: tag a release (pyproject still 0.1.0, no tags); optional
  `crispy-copilot models --flat` (fold the plugin's flattenModel into the
  gateway) for non-wrapper users
- toolbox: pluginRoot dedup in marketplace.json (pre-existing nit); a11y
  polish (pre-existing)
- when the gateway gains features, update Part A and bump; wrapper reads the
  git tree via `gateway_update`, no vendored copy exists (keep it that way)

## Rules of engagement for a new session

1. Identify which repo your task belongs to (service behavior → gateway;
   wrapper/chip/view/tools → zcode-toolbox). The wrapper never patches
   gateway code — it clones and manages the tree.
2. Never touch the OTHER machine's data or the standalone's shared store from
   wrapper code; stores are isolated by design.
3. Secrets discipline: GitHub tokens and key hashes are stripped on read;
   raw `ck_` values appear exactly once (creation). Never log or return them.
4. Windows: re-check the five gotchas above before touching spawn/view code.
5. Verify before pushing: gateway → pytest + smoke.sh; wrapper → imports +
   runtime view smoke + isolated-port lifecycle test. Small verified commits.
