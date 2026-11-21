"""Token exchange against a mocked transport; header contract."""
import time

import httpx

from crispy_copilot_gateway import copilot


def _client(payload, calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=payload)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_exchange_returns_and_caches_token():
    calls = []
    expires = time.time() + 3600
    http = _client({"token": "ctok", "expires_at": expires}, calls)
    assert copilot.get_copilot_token.__name__ == "get_copilot_token"
    import asyncio
    tok = asyncio.run(copilot.get_copilot_token("gh", http))
    tok2 = asyncio.run(copilot.get_copilot_token("gh", http))
    assert tok == tok2 == "ctok"
    assert len(calls) == 1  # second call served from cache


def test_upstream_headers_shape():
    headers = copilot.upstream_headers("ctok")
    assert headers["Authorization"] == "Bearer ctok"
    assert headers["X-GitHub-Api-Version"] == copilot.API_VERSION
    assert headers["Openai-Intent"] == "conversation-edits"
    assert headers["x-initiator"] == "agent"
    assert headers["User-Agent"].startswith("opencode/")
