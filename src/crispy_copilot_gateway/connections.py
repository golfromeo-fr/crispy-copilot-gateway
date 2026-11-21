"""GitHub OAuth device flow (RFC 8628) and locally stored GitHub token connections."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import httpx

from . import config

CLIENT_ID = os.environ.get("CRISPY_COPILOT_CLIENT_ID", "")
DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
_SCOPE = "read:user"
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def start_device_flow() -> dict:
    """Step 1 of RFC 8628: returns verification_uri, user_code, device_code, interval."""
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            DEVICE_URL,
            json={"client_id": CLIENT_ID, "scope": _SCOPE},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


async def poll_once(device_code: str) -> dict:
    """Step 2 of RFC 8628: returns access_token, or an error field such as
    authorization_pending / slow_down / expired_token / incorrect_device_code."""
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            TOKEN_URL,
            json={"client_id": CLIENT_ID, "device_code": device_code, "grant_type": _GRANT_TYPE},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


def save(label: str, github_token: str) -> None:
    stored = load_all()
    stored[label] = {"github_token": github_token, "added_at": _now()}
    config.write_private(config.connections_path(), json.dumps(stored, indent=2))


def load_all() -> dict:
    path = config.connections_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def first_token() -> str | None:
    """First stored GitHub token in insertion order, or None when no connection exists."""
    for entry in load_all().values():
        return entry["github_token"]
    return None
