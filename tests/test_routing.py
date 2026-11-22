"""Per-connection endpoint cache and routing policy (P4 catalog isolation)."""
import asyncio

from crispy_copilot_gateway import connections, keys, routing


def test_cache_isolated_per_connection_label():
    connections.save("work", "tok-a")
    connections.save("alt", "tok-b")
    connections.set_active("work")
    routing.set_cache({"m": ["/chat/completions"]}, label="work")
    routing.set_cache({"m": ["/responses"]}, label="alt")
    keys.create("pinned", connection="alt")
    # a pinned key routes on its own account's catalog
    assert routing.model_endpoints("m", "pinned") == frozenset({"/responses"})
    assert asyncio.run(routing.upstream_kind("m", "pinned")) == "responses"
    # an unpinned key follows the active account
    assert routing.model_endpoints("m") == frozenset({"/chat/completions"})
    assert asyncio.run(routing.upstream_kind("m")) == "chat"


def test_set_cache_default_targets_active_account():
    connections.save("work", "tok-a")
    connections.set_active("work")
    routing.set_cache({"m": ["/responses"]})  # no label -> active account
    assert routing.model_endpoints("m") == frozenset({"/responses"})


def test_stale_pin_falls_back_to_active_catalog():
    connections.save("work", "tok-a")
    connections.set_active("work")
    routing.set_cache({"m": ["/responses"]}, label="work")
    keys.create("pinned", connection="ghost")  # pin to a nonexistent account
    assert routing.model_endpoints("m", "pinned") == frozenset({"/responses"})
