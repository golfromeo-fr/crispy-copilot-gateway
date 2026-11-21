"""Filesystem locations and defaults."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "crispy-copilot-gateway"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8812


def data_dir() -> Path:
    """State directory, overridable via CRISPY_COPILOT_DATA_DIR."""
    override = os.environ.get("CRISPY_COPILOT_DATA_DIR")
    base = Path(override) if override else Path.home() / ".local" / "share" / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def connections_path() -> Path:
    return data_dir() / "connections.json"


def keys_path() -> Path:
    return data_dir() / "api-keys.json"


def write_private(path: Path, text: str) -> None:
    """Write a secret store (tokens, key hashes) with 0600 permissions from creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
