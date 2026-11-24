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

> **Open incident (2026-11-24), required fix pending:** an install cloned before
> gateway `554f8e5` cannot be started by plugin 0.2.3 or later. Workaround:
> `gateway_update`, then `gateway_start`. Details under "Open incident" below.

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
~30 min after ZCode closes. The wrapper enforces that requirement since plugin
0.2.8: `gateway_start` probes the installed gateway and brings a plugin-managed
install up to date first (or starts without the newer feature, and the result
says so). `gateway_update`:
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
Caveat (2026-11-24, read from the code, not executed): `gateway_update` runs
`fetch` and `reset --hard` on `gateway_path` as well, so the "never touched" claim
does not hold yet. Do not point it at a checkout with uncommitted work.

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

**Windows 11 machine** (observed there 2026-11-24, plugin 0.2.7): live in use.
Plugin 0.2.0 installed on 2026-11-23 with a clone of `a54bcf1`; plugin 0.2.7 on
2026-11-24 then failed to start it (see "Open incident"); `gateway_update` +
`gateway_start` at 05:09 to 05:11 left the clone at `bd999d7` and the gateway
healthy on `:8812`. Store intact: one account, one key `zcode`, metering
history. Sessions on that machine: treat it as live, test on a scratch runtime
dir and a free port, and do not call `gateway_stop` or `gateway_update` against
the default instance. Toolchain in a plain shell: Git for Windows, python.org
Python 3.13, and no `node` on PATH (the plugin's own JS runs as the app binary
itself). Pre-0.2.0 leftovers (an old clone, an old store with a copy of the
account token, logs) remain under the plugin dir there and were left alone. No
clone of zcode-toolbox exists there; its marketplace snapshot is app-managed.

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
on that machine, nor whether it has a browser registered at all. Its gateway
clone commit is not verified either (it matters for the incident below).

## Open incident (2026-11-24) — RESOLVED in plugin 0.2.8: an old install cannot be started by a new wrapper

**Symptom.** On an instance whose gateway clone predates gateway `554f8e5`
(2026-11-23 04:45), `gateway_start` fails from plugin 0.2.3 on with "gateway did
not come up within 20s". `serve.log` ends with `No such option: --idle-exit`: the
wrapper passes that option whenever `idle_exit` is above 0, and the old `serve`
only knows `--host` and `--port`.

**Workaround, no code:** `gateway_update` (or the view's update…), then
`gateway_start`. Fixed for good in plugin 0.2.8 (below): start itself heals the
install.

**Why (read from the 0.2.7 code, behaviour seen on the Windows machine).**
`ensureInstall` returns as soon as the venv CLI exists, so an existing clone is
never re-checked; only `gateway_update` runs git, and only when called. The
plugin does not watch the process it spawned, waits the full 20 s, and returns
only a log path. Part B's "needs gateway >= 0.1.1" was documentation, not code.
Every test began from a fresh clone, so "old clone, new wrapper" was never
exercised. The gateway is right to refuse an unknown option; leave that alone.

**Scope.** Any instance whose clone predates `554f8e5`, until `gateway_update`
is run. Known: the Windows machine (healed). The work Linux box and any other
instance are not verified.

**Required fix, shipped in plugin 0.2.8.** The implementation: the extension
declares what it needs at the top of `impl/gateway.js` (`GATEWAY_MIN_VERSION`
plus a `SERVE_OPTIONS` table, one entry per option with the gateway version
that introduced it). `gateway_start` reads the version installed in the venv
(the git tree alone lies when a pip step failed), updates a plugin-managed
install first when an option in use is missing, starts without an option it
cannot get and says so, refuses outright only below the minimum, returns the
gateway's own error at once when it exits during startup, reads log tails
without the /healthz noise, serialises install/update/start across processes
with a lock in the runtime dir, and makes `gateway_update` refuse an external
checkout (with the command to run there) and treat a failed behind-count as an
error. Stop verifies the pid against the process command line and signals only
its own gateway. Tests: toolbox `tools/test-gateway-lifecycle.mjs` (16
hermetic scenarios with a stand-in gateway; `REAL=1` replays this incident
against the real repo) and the view smoke.
1. Before spawning an existing install, probe `<cli> serve --help` for every
   option the wrapper is about to pass. Match the option names only: the output
   is box-drawn.
2. Missing option on the managed install: update the tree (git, then pip: the
   CLI runs the copy in site-packages, not the git tree), re-probe, then start,
   and say what it updated from and to. Share that step with `gateway_update`.
3. If the update cannot run (offline, git failure): start without the optional
   option and say so in the result. For a non-optional option (none today), fail.
4. An external `gateway_path` checkout is never written to: start without the
   optional option when that is safe, otherwise return an error naming the path,
   its commit, the missing option and the commit that adds it.
5. Fail fast: if the gateway exits before `/healthz` answers, return its own
   error at once. `serve.log` grows without bound (about two lines per 5 s while
   a view or chip polls), so read only what was written since the spawn and
   leave out the `/healthz` lines.
6. `gatewayStatus` says whether the installed gateway satisfies this wrapper.
7. One install or update at a time across processes: the view tools run in the
   extension host and the MCP tools in a separate process, and `installPromise`
   is per-process. Use a lock in the runtime dir.

Default decision unless the author says otherwise: probe, heal, then start,
falling back to a reported degraded start. Do not vendor a gateway copy, make
the gateway tolerate unknown options, or fetch on every start.

**Also found with it.**
- `gateway_update` counts the commits behind with a plain `git`, outside the
  hermetic path and without the resolved binary. If that fails, the result reads
  "already on latest". And it resets `gateway_path` too (see the userConfig
  caveat in Part B). Both read from the code, not executed.
- Stop safety on Windows (observed there while the gateway was up): `serve.pid`
  holds the launcher and the port is owned by its grandchild (three processes);
  the file is removed only by `gatewayStop`, so an idle exit or a failed start
  leaves it behind; `gatewayStop` signals whatever PID it names. Not verified
  what `process.kill()` terminates for that chain: check it on Windows before
  redesigning stop. Never signal a PID that is not verified to be this plugin's
  gateway, and do not leave a stale file behind. If `/healthz` gains fields for
  this, the wrapper must treat them as optional, or this incident repeats.

**Tests to add** (toolbox `tools/`, run with node, each on a scratch
`CRISPY_GATEWAY_RUNTIME_DIR` and a free port; ad-hoc tests so far used :8813 to
:8828): the real upgrade path (reset a scratch clone to `a54bcf1`, pip-install
it, start with the fixed wrapper); the decision logic with a fake CLI whose
`serve --help` omits an option; a CLI that exits at once; a stale PID naming a
harmless process; a behind-count that cannot run; the view's outdated-install
state.

**Release.** Plugin 0.2.8 through `release-plugin.sh`, which first copies from
the live install dir: make sure that copy is current before running it.

## Part D — open/optional items

Shipped: the stale-install fix (plugin 0.2.8). Older optional polish:
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
