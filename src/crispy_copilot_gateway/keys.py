"""Proxy API keys: per-harness secrets, stored as SHA-256 hashes only."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime

from . import config

_PREFIX = "ck_"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load() -> list[dict]:
    path = config.keys_path()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save(keys: list[dict]) -> None:
    config.write_private(config.keys_path(), json.dumps(keys, indent=2))


def create(name: str) -> str:
    """Create a per-harness key; returns the raw secret, which is shown once."""
    keys = _load()
    if any(entry["name"] == name for entry in keys):
        raise ValueError(f"proxy key name already exists: {name}")
    raw = _PREFIX + secrets.token_urlsafe(32)
    keys.append({"name": name, "key_hash": _hash(raw), "created_at": _now(), "last_used": None})
    _save(keys)
    return raw


def verify(raw: str) -> bool:
    """Return True and stamp last_used (throttled) when raw matches a stored key hash."""
    if not raw:
        return False
    digest = _hash(raw)
    keys = _load()
    for entry in keys:
        if secrets.compare_digest(entry["key_hash"], digest):
            if _stamp_due(entry):
                entry["last_used"] = _now()
                _save(keys)
            return True
    return False


_LAST_USED_INTERVAL = 60  # seconds between last_used writes per key


def _stamp_due(entry: dict) -> bool:
    stamp = entry.get("last_used")
    if stamp is None:
        return True
    age = datetime.now(UTC) - datetime.fromisoformat(stamp)
    return age.total_seconds() >= _LAST_USED_INTERVAL


def revoke(name: str) -> bool:
    keys = _load()
    remaining = [entry for entry in keys if entry["name"] != name]
    if len(remaining) == len(keys):
        return False
    _save(remaining)
    return True


def list_keys() -> list[dict]:
    """Key metadata without hashes, for the management UI."""
    return [
        {"name": entry["name"], "created_at": entry["created_at"], "last_used": entry["last_used"]}
        for entry in _load()
    ]
