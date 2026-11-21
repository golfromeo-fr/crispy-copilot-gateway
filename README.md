# crispy-copilot-gateway

Local API gateway for GitHub Copilot - universal Anthropic/OpenAI endpoints for any harness, with a management UI. Status: WIP, Phase 1.

## How it works

- GitHub OAuth device flow runs once in the browser; the long-lived GitHub token is stored in a 0600 file.
- The gateway derives the short-lived (~30 min) Copilot inference token from it and owns its refresh.
- Per-harness proxy keys: named, revocable, sha256-hashed at rest; the raw key is shown once.
- SSE streams pass through untouched.

## Endpoints

| Path | Speaks | Serves |
| --- | --- | --- |
| `/v1/chat/completions` | OpenAI chat | ZCode, opencode, most tools |
| `/v1/responses` | OpenAI Responses | Codex, newer clients |
| `/v1/messages` | Anthropic Messages | Claude Code (P2) |
| `/v1/models` | OpenAI model list | all |

## Quickstart (planned)

```bash
pip install -e .
crispy-copilot login work
crispy-copilot key zcode
crispy-copilot serve
```

## Security

- Binds 127.0.0.1 by default; a proxy key is required on every endpoint.
- The GitHub token and proxy keys live in 0600 files under `~/.local/share/crispy-copilot-gateway`.
- No token-bearing data is written to logs.

## Phases

- P1: core proxy - device flow, token exchange, OpenAI passthrough.
- P2: Anthropic `/v1/messages` route.
- P3: NiceGUI management UI.
- P4: multi-account - per-key connection mapping.

## License

MIT
