# crispy-copilot-gateway

Local API gateway for GitHub Copilot - universal Anthropic/OpenAI endpoints for any harness, with a management UI and credits metering. Status: P1-P4 built, unit-tested, and live-verified against Copilot.

## How it works

- GitHub OAuth device flow runs once in the browser; the long-lived GitHub token is stored in a 0600 file.
- The gateway sends that GitHub token directly as the Copilot bearer (opencode-compatible; no token exchange) and mimics opencode's client fingerprint.
- Per-harness proxy keys: named, revocable, sha256-hashed at rest; the raw key is shown once.
- Native routes pass SSE through byte-identical; models served only via `/responses` are adapted (chat/Anthropic <-> Responses) and re-encoded. A best-effort tap records Copilot credits (`copilot_usage.total_nano_aiu`) to a local SQLite store.
- Each proxy key can pin a GitHub account (P4); unpinned keys follow the active account, and routing uses that account's own model catalog.
- If Copilot advertises `/chat/completions` but rejects it at request time, the gateway retries once via `/responses`. Image-bearing requests are detected across chat/Responses/Anthropic shapes and flagged with Copilot's vision header.
- Upstream errors pass through with their original status and body - never re-encoded into a success-shaped reply.

## Endpoints

| Path | Speaks | Serves |
| --- | --- | --- |
| `/v1/chat/completions` | OpenAI chat | ZCode, opencode, most tools |
| `/v1/responses` | OpenAI Responses | Codex, newer clients |
| `/v1/messages` | Anthropic Messages | Claude Code, ZCode |
| `/v1/messages/count_tokens` | Anthropic token count | Claude Code (rough estimate) |
| `/v1/models` | OpenAI model list | all |

## Quickstart

```bash
./setup.sh        # venv + install + first ck_ key (Enter accepts opencode's OAuth client ID)
./start.sh        # serve API + UI on http://127.0.0.1:8812
# open http://127.0.0.1:8812/ui, log in (any username + a ck_ key), then
# "Connect via browser" to link your GitHub account (device-flow code).
./smoke.sh <ck_key>   # end-to-end: healthz -> models -> real chat -> credits recorded
```

## Management UI

`./start.sh` also serves a management UI at http://127.0.0.1:8812/ui. The browser asks for
credentials: enter any username and one of your `ck_` proxy keys as the password. There you
can connect/disconnect GitHub accounts (device-flow login from the browser), create or revoke
proxy keys, and see a **Copilot usage** card (requests, credits burned, estimated USD).

## Testing

With the gateway running and a GitHub account connected, run `./smoke.sh <ck_key> [model]`.
It checks auth, lists models, sends a real chat completion through Copilot, prints the
reply plus usage, and asserts a credits row was recorded in `usage.db`. The UI usage card
shows the same totals.

## Security

- Binds 127.0.0.1 by default; a proxy key is required on every API endpoint (`/healthz` is open for liveness checks). The management UI authenticates via HTTP Basic.
- The GitHub token and proxy keys live in 0600 files under `~/.local/share/crispy-copilot-gateway`.
- No token-bearing data is written to logs.

## Phases

- P1 (done): core proxy - device flow, direct-bearer OpenAI passthrough, per-connection endpoint routing.
- P2 (done): Anthropic `/v1/messages` route + chat/Anthropic <-> Responses adaptation for `/responses`-only models.
- P3 (done): NiceGUI management UI + Copilot credits metering.
- P4 (done): multi-account - per-key connection mapping (each proxy key can pin a GitHub account; unpinned keys follow the active account).

## License

MIT
