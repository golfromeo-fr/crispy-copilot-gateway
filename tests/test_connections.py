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
