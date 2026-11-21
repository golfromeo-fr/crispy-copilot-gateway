"""Filesystem locations and defaults."""
from __future__ import annotations

import os
import secrets
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


def storage_secret() -> str:
    """Persistent NiceGUI storage secret: generated once, kept 0600 in the data dir."""
    path = data_dir() / "storage_secret.txt"
    if not path.exists():
        write_private(path, secrets.token_urlsafe(32))
    return path.read_text(encoding="utf-8").strip()


def write_private(path: Path, text: str) -> None:
    """Atomically write a secret store with 0600 perms: temp file, fsync, os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
