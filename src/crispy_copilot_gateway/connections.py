"""GitHub OAuth device flow (RFC 8628) and locally stored GitHub token connections."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import httpx

from . import config, copilot, keys

# Default: opencode's registered Copilot OAuth client ID (from
# /home/gr/opencode/packages/core/src/plugin/provider/github-copilot.ts). Using it
# makes the device-flow grant identical to an opencode login; override with
# CRISPY_COPILOT_CLIENT_ID only if you register your own app.
CLIENT_ID = os.environ.get("CRISPY_COPILOT_CLIENT_ID", "Ov23li8tweQw6odWQebz")
DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/copilot_internal/user"
USER_API_VERSION = "2025-04-01"  # opencode's userApiVersion constant
_SCOPE = "read:user"
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _gh_headers() -> dict:
    """Headers opencode sends on github.com OAuth and api.github.com calls."""
    return {
        "Accept": "application/json",
        "User-Agent": copilot.USER_AGENT,
        "X-GitHub-Api-Version": USER_API_VERSION,
    }


async def start_device_flow() -> dict:
    """Step 1 of RFC 8628: returns verification_uri, user_code, device_code, interval."""
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            DEVICE_URL,
            json={"client_id": CLIENT_ID, "scope": _SCOPE},
            headers=_gh_headers(),
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
            headers=_gh_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def verify_entitlement(github_token: str) -> dict:
    """Read Copilot access + endpoint from /copilot_internal/user, like opencode at login.

    Returns the raw user payload; callers check chat_enabled (False without Copilot)
    and may store endpoints.api as the account's inference endpoint.
    """
    async with httpx.AsyncClient() as http:
        resp = await http.get(USER_URL, headers={**_gh_headers(), "Authorization": f"Bearer {github_token}"})
        resp.raise_for_status()
        return resp.json()


def save(label: str, github_token: str, api_endpoint: str | None = None) -> None:
    stored = load_all()
    entry = {"github_token": github_token, "added_at": _now()}
    if api_endpoint:
        entry["api_endpoint"] = api_endpoint.rstrip("/")
    stored[label] = entry
    config.write_private(config.connections_path(), json.dumps(stored, indent=2))


def load_all() -> dict:
    path = config.connections_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def active_label() -> str | None:
    """Label of the user-selected active connection, or None when unset/stale."""
    path = config.active_path()
    if not path.exists():
        return None
    try:
        label = json.loads(path.read_text(encoding="utf-8")).get("label")
    except Exception:
        return None
    # A stale label (account removed) must not present itself as active;
    # routing falls back to the first entry.
    return label if label in load_all() else None


def effective_active_label() -> str | None:
    """What routing actually uses: the selected label, else the first entry's."""
    return active_label() or next(iter(load_all()), None)


def set_active(label: str) -> bool:
    """Mark label active (0600 store); only when the label exists."""
    if label not in load_all():
        return False
    config.write_private(config.active_path(), json.dumps({"label": label}))
    return True


def active_connection() -> dict | None:
    """The stored label's entry, falling back to the first entry (insertion order)."""
    stored = load_all()
    label = active_label()
    if label is not None and label in stored:
        return stored[label]
    for entry in stored.values():
        return entry
    return None


def first_token() -> str | None:
    entry = active_connection()
    return entry["github_token"] if entry else None


def for_key(key_name: str | None) -> dict | None:
    """Connection a proxy key routes to (P4): its pinned label, else the active account.

    A stale pin (account removed) falls back to the active account rather than failing.
    """
    label = keys.connection_of(key_name) if key_name else None
    stored = load_all()
    if label and label in stored:
        return stored[label]
    return active_connection()


def label_for_key(key_name: str | None) -> str | None:
    """Effective connection label for a key: its pin if present, else the active label."""
    label = keys.connection_of(key_name) if key_name else None
    if label and label in load_all():
        return label
    return effective_active_label()


def remove(label: str) -> bool:
    stored = load_all()
    if label not in stored:
        return False
    del stored[label]
    config.write_private(config.connections_path(), json.dumps(stored, indent=2))
    # Clear a stale active label so the UI doesn't claim a removed account is
    # active; routing already falls back to the first entry.
    if active_label() == label:
        config.active_path().unlink(missing_ok=True)
    return True


async def complete_login(label: str, github_token: str) -> dict:
    """Verify entitlement (tolerating hiccups like opencode), then store the connection."""
    user = None
    try:
        user = await verify_entitlement(github_token)
    except Exception:
        user = None  # a failed lookup must not turn a GitHub hiccup into a denial
    if user is not None and user.get("chat_enabled") is False:
        raise ValueError("GitHub account has no Copilot access")
    api = (user or {}).get("endpoints", {}).get("api")
    save(label, github_token, api)
    set_active(label)  # a freshly connected account becomes the active one
    return {"label": label, "api_endpoint": api}
