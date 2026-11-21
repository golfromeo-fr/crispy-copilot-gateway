"""Upstream request headers for api.githubcopilot.com (opencode-compatible)."""
from __future__ import annotations

import json
import uuid

BASE_URL = "https://api.githubcopilot.com"
API_VERSION = "2026-08-01"  # opencode's apiVersion constant
# Present as an opencode client end-to-end; the OAuth grant uses opencode's client ID.
USER_AGENT = "opencode/stable/2.2.7/opencode"


def upstream_headers(github_token: str, *, vision: bool = False,
                     interaction_id: str | None = None) -> dict[str, str]:
    """opencode sends the GitHub OAuth token directly as the bearer (no token exchange)."""
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/json",
        "X-GitHub-Api-Version": API_VERSION,
        "Openai-Intent": "conversation-edits",
        "x-initiator": "agent",
        "X-Interaction-Type": "conversation-agent",
        "X-Interaction-Id": interaction_id or str(uuid.uuid4()),
        "User-Agent": USER_AGENT,
    }
    if vision:
        headers["Copilot-Vision-Request"] = "true"
    return headers


def _has_image_parts(parts) -> bool:
    if not isinstance(parts, list):
        return False
    return any(isinstance(p, dict) and p.get("type") in
               ("image_url", "input_image", "image") for p in parts)


def has_vision(body: bytes) -> bool:
    """Detect image parts across OpenAI chat, OpenAI responses, and Anthropic shapes."""
    try:
        obj = json.loads(body)
    except Exception:
        return False
    if not isinstance(obj, dict):
        return False
    for message in obj.get("messages") or []:
        if isinstance(message, dict) and _has_image_parts(message.get("content")):
            return True
    for item in obj.get("input") or []:
        if isinstance(item, dict) and _has_image_parts(item.get("content")):
            return True
    return False
