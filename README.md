# crispy-copilot-gateway

Local API gateway for GitHub Copilot - universal Anthropic/OpenAI endpoints for any harness, with a management UI and credits metering. Status: P1 + P3 built and unit-tested; live Copilot verification pending.

## How it works

- GitHub OAuth device flow runs once in the browser; the long-lived GitHub token is stored in a 0600 file.
- The gateway sends that GitHub token directly as the Copilot bearer (opencode-compatible; no token exchange) and mimics opencode's client fingerprint.
- Per-harness proxy keys: named, revocable, sha256-hashed at rest; the raw key is shown once.
- SSE streams pass through byte-identical; a best-effort tap records Copilot credits (`copilot_usage.total_nano_aiu`) to a local SQLite store.

## Endpoints

| Path | Speaks | Serves |
| --- | --- | --- |
| `/v1/chat/completions` | OpenAI chat | ZCode, opencode, most tools |
| `/v1/responses` | OpenAI Responses | Codex, newer clients |
| `/v1/messages` | Anthropic Messages | Claude Code (P2) |
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
It checks auth, lists models, sends a real chat completion through Copilot, and prints the
reply plus usage. The UI usage card and `usage.db` record the credits for each request.

## Security

- Binds 127.0.0.1 by default; a proxy key is required on every endpoint.
- The GitHub token and proxy keys live in 0600 files under `~/.local/share/crispy-copilot-gateway`.
- No token-bearing data is written to logs.

## Phases

- P1 (done): core proxy - device flow, direct-bearer OpenAI passthrough, per-connection endpoint routing.
- P3 (done): NiceGUI management UI + Copilot credits metering.
- P2: Anthropic `/v1/messages` route (Claude Code).
- P4: multi-account - per-key connection mapping.

## License

MIT
