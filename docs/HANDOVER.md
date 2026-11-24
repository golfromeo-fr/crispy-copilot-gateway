# Handover — crispy-copilot-gateway + ZCode wrapper

Complete context for a fresh chat/session touching either the gateway (this
repo) or its ZCode wrapper plugin. Written 2026-11-23; facts pinned to commit
SHAs and versions — re-verify anything that looks stale.

**Repos**
- Gateway (this repo): `golfromeo-fr/crispy-copilot-gateway`, last code commit
  `1562113` (0.1.1); docs-only commits after it
- Wrapper plugin: `golfromeo-fr/zcode-toolbox`, `plugins/copilot-gateway`
  v0.2.7 @ `e8e318f` (marketplace: `golfromeo-zcode-toolbox`; sibling plugins
  context-lens 1.6.2, token-speed 1.2.1)

## Part A — the gateway (this repo)

Local API gateway for GitHub Copilot. Serves on `127.0.0.1:8812` (default):

| Path | Speaks | Auth |
|---|---|---|
| `/healthz` | `{"ok":true}` | none (only unauth endpoint) |
| `serve --idle-exit MIN` | graceful shutdown after MIN minutes without any request (0 = never, the CLI default) | — |
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
`gateway_login {label}` returns code+url and `openedInBrowser`, true only
when an opener really launched a browser; `login_check` phases:
`awaiting-authorization` pending · `connected` · `expired` (codes live ~15
min: start again) · `failed` (see `logTail`)) · keys · key_create / key_revoke / key_pin ·
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
hints), Connect GitHub account (login…: address + code as plain text, copy
buttons that report success), Accounts,
Pending logins, Proxy keys (pin select + revoke), Copilot credits (totals +
recent), Harness config (**config…** regenerate for existing key ·
**new key…** first-time · **copy** to clipboard), update…. The `updated`
line shows the bound bridge global (`· bridge zcode|host`) for debugging.
Plain links cannot leave a plugin view: the app allows navigation only to
its own origins and denies new windows (read in the app bundle's main
process, 2026-11-23). The login link (card and Pending logins row, plugin
0.2.6) is therefore opened through the view bridge's
`openExternal({href})`, which the app hands to its standard open-URL path and
so to the system browser. That call never reports failure to the page (the
app only logs it), so the link also stays on screen as text and the message
keeps "type it" as the fallback.

### Login when no browser opens here (VMs, remote desktops)

The address to enter the code is whatever GitHub returns with it
(`verification_uri`): `https://github.com/login/device` for github.com as
observed on 2026-11-23 and shown in GitHub's docs, which is an observation, not
a guarantee, and there is no pre-filled link in the response. The plugin does
not assume it: the view shows only the address that came with the code, and
nothing opens a guessed address. The login card shows the link and the code
as text as soon as a login is started, whatever the opener reported, because
an opener can exit cleanly without anything appearing. Clicking the link asks
the app to open it in the system browser (plugin 0.2.6). On a machine where
the OS has no browser registered, no plugin-side route can open the page (the
plugin's own launch at login time and the app's open-URL call both end at the
system browser): there the link has to be carried to a browser elsewhere by
hand or clipboard. When no browser opened, the card also shows the terminal
command for that machine with the link (plugin 0.2.7): `xdg-open` on Linux,
`open` on macOS, `start` on Windows — the status and login results carry the
gateway's `platform`. Run in a terminal, it opens the page when a browser is
installed and prints the reason when none is. The device flow does
not care which machine the browser is on. When `openedInBrowser` is false or the clipboard is not
shared, type the address shown in the view by hand in any browser signed in to
that GitHub account (phone, host, another computer) and type the code. Codes
expire after ~15 minutes:
start a new login instead of retrying an old one. Terminal fallback on the
machine itself (the env var is what keeps the token in the plugin's store):

    CRISPY_COPILOT_DATA_DIR=~/.zcode/plugin-data/copilot-gateway/store \
      ~/.zcode/plugin-data/copilot-gateway/gateway/.venv/bin/crispy-copilot login <label>

Verified: a running gateway serves the new account without a restart (503 →
upstream auth response as soon as `connections.json` appears).

The gateway's device-flow and token endpoints are fixed to github.com
(`connections.py`): it cannot sign in to a GitHub Enterprise Server host.

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
Release rule: bump the manifest AND marketplace version in a sequence that
halts on failure, stage both files, and after pushing read both back from the
pushed commit (`git show origin/main:<file>`), not from the working tree. The
0.2.4 bump first went out with the marketplace entry left unstaged. The 0.2.2 bump
was skipped by a script that stopped earlier while the commit and push went
on: the idle-exit code shipped labelled 0.2.1, so no instance was offered it.

Verification patterns that caught real bugs: runtime view smoke
`node tools/smoke-copilot-gateway-view.mjs` in the toolbox (runs the real view
script under browser-faithful stubs with a zcode-only bridge, drives the click
and refresh paths, fails on duplicate element ids — `node --check` alone is
NOT enough), isolated-port lifecycle tests (:8813-:8828 used so far), temp-key
create/revoke for catalog reads, planted-legacy-store migration test, fake
openers on PATH for the browser-open result.

## Part C — machine states (as of 2026-11-23)

Plugin releases since 0.2.0: 0.2.1 sticky copy bar (its sticky button had no
click handler until 0.2.3); **0.2.2 was never published as a version** (the
idle-exit code is in toolbox `91a5d4a`, but the bump was skipped, see above);
0.2.3 (login without a browser or clipboard, copy feedback, idle-exit code);
0.2.4 (the plugin no longer assumes the device address); 0.2.5 (login card:
the link is always the fallback when no browser opens); 0.2.6 (the link is
clickable again, opened through the app's open-URL call); 0.2.7 (the card
shows the terminal command for the machine when no browser opened).

**Home Linux box** (re-verified 2026-11-23): standalone gateway serving
`:8812` from `~/crispy-copilot-gateway` with the shared store (accounts +
keys + credit history intact — plugin work never touched it; 132 unit tests
pass). A copy of the plugin sits at `~/.zcode/plugins/copilot-gateway` (code
as of toolbox `91a5d4a`, labelled 0.2.1, not updated since) but is NOT
installed in that ZCode instance; test machines update from GitHub. The runtime dir
`~/.zcode/plugin-data/copilot-gateway` is **absent**, and no legacy in-plugin
store remains — earlier "install + `zcode` key, no account" state is gone.
First `gateway_start` here = fresh clone + venv + `login` from scratch.

**Windows 11 VM**: full working setup — plugin installed, gateway installed
via the wrapper, account connected (device flow), credits metering observed
(6 req), ZCode wired by pasting the provider JSON into a chat. After
updating to 0.2.0 the migration should preserve the account.

**Work Linux box** (2026-11-23): ZCode with the plugin under test. The user
could not open the GitHub link from that session and could not copy the view
text out. What they needed was for the extension itself to show the link when
no browser opens (stated 2026-11-23), not advice to type an address by hand.
The login card has shown the link and the code as text since plugin 0.2.3
(only the link GitHub returned with the code) and makes the link the explicit
fallback in 0.2.5; it also explains expired or failed attempts. The same day
the user asked to revert to "the link that was working on a previous test":
that was a plain link, which cannot open from a plugin view; what opened a
browser in the Windows test was the plugin's launch at login time. 0.2.6
makes the link clickable through the app's open-URL call. Not yet confirmed
on that machine, nor whether it has a browser registered at all.

## Part D — open/optional items

Nothing required. Optional polish, either repo:
- gateway: tag a release (pyproject 0.1.1, no tags); optional
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
6. Tests: point `CRISPY_GATEWAY_RUNTIME_DIR` (and `CRISPY_GATEWAY_PORT`) at
   scratch values. On the home box the default runtime dir is meant to stay
   absent until the plugin is actually installed there.
7. Deploying to a live plugin dir means copying the changed files — never an
   `rm` and re-copy — and saying what a destructive command does before
   running it. Replacing the plugin dir wholesale is what destroyed the
   plugin's OAuth account here while the store still lived inside it.
