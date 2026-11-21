"""Gateway auth, connection guard, and forwarding against a mocked upstream."""
import time

import httpx
from fastapi.testclient import TestClient

from crispy_copilot_gateway import proxy


def _make_client(monkeypatch, *, connected=True):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/copilot_internal/v2/token" or "copilot_internal" in str(request.url):
            return httpx.Response(200, json={"token": "ctok", "expires_at": time.time() + 3600})
        if request.url.path == "/chat/completions":
            # MockTransport pre-consumes bytes content under send(stream=True);
            # an async generator keeps the stream consumable for aiter_raw().
            async def sse():
                yield b"data: {\"x\":1}\n\n"
                yield b"data: [DONE]\n\n"
            return httpx.Response(200, content=sse(),
                                  headers={"content-type": "text/event-stream"})
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": "gpt-5"}]})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.githubcopilot.com")
    monkeypatch.setattr(proxy, "upstream", mock)
    from crispy_copilot_gateway import connections
    if connected:
        connections.save("work", "gh-token")
    else:
        monkeypatch.setattr(connections, "first_token", lambda: None)
    return TestClient(proxy.build_app()), calls


def _auth(client):
    from crispy_copilot_gateway import keys
    return {"Authorization": f"Bearer {keys.create('t')}"}


def test_healthz_open_without_key(monkeypatch):
    client, _ = _make_client(monkeypatch)
    assert client.get("/healthz").json() == {"ok": True}


def test_unauthenticated_requests_are_401(monkeypatch):
    client, _ = _make_client(monkeypatch)
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"x-api-key": "ck_wrong"}).status_code == 401


def test_connected_key_guards_503_when_no_connection(monkeypatch):
    client, _ = _make_client(monkeypatch, connected=False)
    resp = client.get("/v1/models", headers=_auth(client))
    assert resp.status_code == 503
    assert "no GitHub connection" in resp.json()["detail"]


def test_models_forwarded_with_copilot_bearer(monkeypatch):
    client, calls = _make_client(monkeypatch)
    resp = client.get("/v1/models", headers=_auth(client))
    assert resp.status_code == 200 and resp.json() == {"data": [{"id": "gpt-5"}]}
    upstream_model_call = calls[-1]
    assert upstream_model_call.headers["Authorization"] == "Bearer ctok"
    assert upstream_model_call.headers["X-GitHub-Api-Version"] == proxy.copilot.API_VERSION


def test_sse_chat_passthrough_streams_body(monkeypatch):
    client, calls = _make_client(monkeypatch)
    resp = client.post("/v1/chat/completions", headers=_auth(client), json={"model": "gpt-5"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert b"data: [DONE]" in resp.content
    sent = calls[-1]
    assert sent.headers["Openai-Intent"] == "conversation-edits"
    assert sent.method == "POST"
