"""Copilot inference-token exchange and upstream request headers."""

from __future__ import annotations

import time

import httpx

from . import __version__

BASE_URL = "https://api.githubcopilot.com"
API_VERSION = "2026-08-01"  # matches opencode's apiVersion constant in github-copilot.ts
TOKEN_ENDPOINT = "https://api.github.com/copilot_internal/v2/token"
_REFRESH_MARGIN = 60  # refresh when fewer than this many seconds remain

# NOTE (P1 live verification): opencode's provider performs NO /copilot_internal/v2/token
# exchange - it sends the GitHub OAuth access token directly as the upstream bearer. The
# VS Code editor flow does exchange at TOKEN_ENDPOINT, but the payload field names
# {"token", "expires_at"} (expires_at assumed epoch seconds) must be verified live in P1.
_tokens: dict[str, tuple[str, float]] = {}  # github_token -> (copilot_token, expires_at)


async def get_copilot_token(github_token: str, http: httpx.AsyncClient) -> str:
    """Exchange (cached) a GitHub token for a short-lived Copilot inference token."""
    cached = _tokens.get(github_token)
    if cached and cached[1] - time.time() >= _REFRESH_MARGIN:
        return cached[0]
    resp = await http.get(
        TOKEN_ENDPOINT,
        headers={"Accept": "application/json", "Authorization": f"Bearer {github_token}"},
    )
    resp.raise_for_status()
    payload = resp.json()
    copilot_token = payload["token"]
    _tokens[github_token] = (copilot_token, float(payload["expires_at"]))
    return copilot_token


def upstream_headers(copilot_token: str) -> dict[str, str]:
    """Headers for api.githubcopilot.com, mirroring opencode's applyHeaders()."""
    return {
        "Authorization": f"Bearer {copilot_token}",
        "Accept": "application/json",
        "X-GitHub-Api-Version": API_VERSION,
        "Openai-Intent": "conversation-edits",
        "User-Agent": f"crispy-copilot-gateway/{__version__}",
    }
