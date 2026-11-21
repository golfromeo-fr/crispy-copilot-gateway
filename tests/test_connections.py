"""Connection storage round-trip; device-flow URLs hit only in live P1."""
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
