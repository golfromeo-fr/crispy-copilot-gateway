"""Gateway auth, connection guard, forwarding, and metering against a mocked upstream."""
import asyncio

import httpx
from fastapi.testclient import TestClient

from crispy_copilot_gateway import connections, keys, proxy, usage


def _make_client(monkeypatch, *, connected=True):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/chat/completions":
            # MockTransport pre-consumes bytes under send(stream=True); an async
            # generator keeps the stream consumable for aiter_raw().
            async def sse():
                yield b'data: {"x":1}\n\n'
                yield (b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5},'
                       b'"copilot_usage":{"total_nano_aiu":4473525000}}\n\n')
                yield b"data: [DONE]\n\n"
            return httpx.Response(200, content=sse(),
                                  headers={"content-type": "text/event-stream"})
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": "gpt-5"}]})
        return httpx.Response(404)

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.githubcopilot.com")
    monkeypatch.setattr(proxy, "upstream", mock)
    if connected:
        connections.save("work", "gh-token")
    # else: no connection stored -> active_connection() is None naturally
    return TestClient(proxy.build_app()), calls


def _auth():
    return {"Authorization": f"Bearer {keys.create('t')}"}


def test_healthz_open_without_key(monkeypatch):
    client, _ = _make_client(monkeypatch)
    assert client.get("/healthz").json() == {"ok": True}


def test_unauthenticated_requests_are_401(monkeypatch):
    client, _ = _make_client(monkeypatch)
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"x-api-key": "ck_wrong"}).status_code == 401


def test_503_when_no_connection(monkeypatch):
    client, _ = _make_client(monkeypatch, connected=False)
    resp = client.get("/v1/models", headers=_auth())
    assert resp.status_code == 503
    assert "no GitHub connection" in resp.json()["detail"]


def test_models_forwarded_with_github_bearer(monkeypatch):
    client, calls = _make_client(monkeypatch)
    resp = client.get("/v1/models", headers=_auth())
    assert resp.status_code == 200 and resp.json() == {"data": [{"id": "gpt-5"}]}
    sent = calls[-1]
    assert sent.headers["Authorization"] == "Bearer gh-token"
    assert sent.headers["X-GitHub-Api-Version"] == proxy.copilot.API_VERSION
    assert sent.headers["X-Interaction-Type"] == "conversation-agent"


def test_per_connection_endpoint_routing(monkeypatch):
    client, calls = _make_client(monkeypatch, connected=False)
    connections.save("ent", "gh-token", api_endpoint="https://copilot-api.example.com")
    resp = client.get("/v1/models", headers=_auth())
    assert resp.status_code == 200
    assert calls[-1].url.host == "copilot-api.example.com"


def test_sse_chat_passthrough_streams_body(monkeypatch):
    client, calls = _make_client(monkeypatch)
    resp = client.post("/v1/chat/completions", headers=_auth(), json={"model": "gpt-5"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert b"data: [DONE]" in resp.content
    sent = calls[-1]
    assert sent.headers["Openai-Intent"] == "conversation-edits"
    assert sent.method == "POST"


def test_chat_completion_records_usage(monkeypatch):
    client, _ = _make_client(monkeypatch)
    resp = client.post("/v1/chat/completions", headers=_auth(), json={"model": "gpt-5"})
    assert resp.status_code == 200
    t = usage.totals()
    assert t["requests"] >= 1
    assert t["input_tokens"] >= 10 and t["output_tokens"] >= 5
    assert abs(t["cost_usd"] - 0.04473525) < 1e-9


def test_fetch_models_uses_active_connection_bearer(monkeypatch):
    client, calls = _make_client(monkeypatch)
    models = asyncio.run(proxy.fetch_models())
    assert models == [{"id": "gpt-5"}]
    sent = calls[-1]
    assert sent.method == "GET" and sent.url.path == "/models"
    assert sent.headers["Authorization"] == "Bearer gh-token"
