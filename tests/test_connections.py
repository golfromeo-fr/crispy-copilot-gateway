"""Connection storage round-trip; device-flow URLs hit only in live P1."""
import asyncio

import pytest

from crispy_copilot_gateway import config, connections


def test_save_and_load_roundtrip_keeps_token_private():
    connections.save("work", "gh-token-1")
    loaded = connections.load_all()
    assert loaded["work"]["github_token"] == "gh-token-1"
    assert config.connections_path().stat().st_mode & 0o777 == 0o600


def test_first_token_follows_insertion_order():
    connections.save("work", "tok-a")
    connections.save("personal", "tok-b")
    assert connections.first_token() == "tok-a"


def test_first_token_none_when_empty():
    assert connections.first_token() is None


def test_save_overwrites_same_label():
    connections.save("work", "old")
    connections.save("work", "new")
    assert connections.first_token() == "new"


def test_complete_login_stores_endpoint(monkeypatch):
    async def fake(token):
        return {"chat_enabled": True, "endpoints": {"api": "https://x"}}
    monkeypatch.setattr(connections, "verify_entitlement", fake)
    result = asyncio.run(connections.complete_login("work", "gh-token"))
    assert result == {"label": "work", "api_endpoint": "https://x"}
    assert connections.load_all()["work"]["api_endpoint"] == "https://x"


def test_complete_login_rejects_no_copilot(monkeypatch):
    async def fake(token):
        return {"chat_enabled": False}
    monkeypatch.setattr(connections, "verify_entitlement", fake)
    with pytest.raises(ValueError):
        asyncio.run(connections.complete_login("work", "gh-token"))
    assert connections.load_all() == {}


def test_complete_login_tolerates_lookup_failure(monkeypatch):
    async def fake(token):
        raise RuntimeError("hiccup")
    monkeypatch.setattr(connections, "verify_entitlement", fake)
    asyncio.run(connections.complete_login("work", "gh-token"))
    assert connections.load_all()["work"]["github_token"] == "gh-token"


def test_remove_roundtrip():
    connections.save("work", "tok")
    assert connections.remove("work") is True
    assert connections.load_all() == {}
    assert connections.remove("work") is False


def test_set_active_roundtrip_writes_private():
    connections.save("work", "tok-a")
    assert connections.set_active("work") is True
    assert config.active_path().stat().st_mode & 0o777 == 0o600
    assert connections.active_label() == "work"
    assert connections.active_connection()["github_token"] == "tok-a"


def test_active_connection_prefers_stored_label():
    connections.save("work", "tok-a")
    connections.save("personal", "tok-b")
    connections.set_active("personal")
    assert connections.active_connection()["github_token"] == "tok-b"


def test_active_connection_falls_back_without_active_file():
    connections.save("work", "tok-a")
    connections.save("personal", "tok-b")
    assert connections.active_label() is None
    assert connections.active_connection()["github_token"] == "tok-a"  # insertion order


def test_active_connection_falls_back_when_label_stale():
    connections.save("work", "tok-a")
    connections.save("personal", "tok-b")
    connections.set_active("personal")
    connections.remove("personal")
    # remove() clears the stale active label so the UI never claims a removed
    # account is active; routing still falls back to the first entry.
    assert connections.active_label() is None
    assert connections.active_connection()["github_token"] == "tok-a"


def test_set_active_unknown_label_returns_false():
    connections.save("work", "tok-a")
    assert connections.set_active("ghost") is False
    assert connections.active_label() is None  # active.json never written


def test_complete_login_activates_new_connection(monkeypatch):
    async def fake(token):
        return {"chat_enabled": True}
    monkeypatch.setattr(connections, "verify_entitlement", fake)
    asyncio.run(connections.complete_login("fresh", "tok-f"))
    assert connections.active_label() == "fresh"
    assert connections.active_connection()["github_token"] == "tok-f"


def test_remove_clears_stale_active_label():
    connections.save("work", "tok-a")
    connections.save("personal", "tok-b")
    connections.set_active("personal")
    assert connections.remove("personal") is True
    import os
    assert connections.active_label() is None  # stale label cleared
    assert connections.active_connection()["github_token"] == "tok-a"  # falls back to first


def test_effective_active_label_prefers_selection_then_first():
    connections.save("work", "tok-a")
    connections.save("personal", "tok-b")
    assert connections.effective_active_label() == "work"  # no selection -> first
    connections.set_active("personal")
    assert connections.effective_active_label() == "personal"
    connections.remove("personal")
    assert connections.effective_active_label() == "work"  # stale selection ignored
