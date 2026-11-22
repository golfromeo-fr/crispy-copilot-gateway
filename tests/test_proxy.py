"""Gateway auth, connection guard, forwarding, and metering against a mocked upstream."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from crispy_copilot_gateway import adapt, connections, keys, proxy, routing, usage

FALLBACK_ARMED = {"on": False}  # fallback tests arm it; handler then 400s mai-code-2-flash


@pytest.fixture()
def arm_fallback():
    FALLBACK_ARMED["on"] = True
    yield
    FALLBACK_ARMED["on"] = False


def _make_client(monkeypatch, *, connected=True):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/chat/completions":
            try:
                cbody = json.loads(request.content or b"{}")
            except Exception:
                cbody = {}
            if FALLBACK_ARMED["on"] and cbody.get("model") == "mai-code-2-flash":
                # Live evidence: advertised /chat/completions rejected at request time.
                return httpx.Response(400, json={"error": {
                    "message": "no model endpoints available given user constraints",
                    "code": "no_available_model_endpoints"}})
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
        if request.url.path == "/responses":
            try:
                rbody = json.loads(request.content or b"{}")
            except Exception:
                rbody = {}
            if rbody.get("stream"):
                async def rsse():
                    yield (b'data: {"type":"response.created","response":{"id":"r9",'
                           b'"model":"gpt-6-luna","usage":{"input_tokens":7}}}\n\n')
                    yield (b'data: {"type":"response.output_item.added",'
                           b'"item":{"type":"message","role":"assistant"}}\n\n')
                    yield b'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
                    yield (b'data: {"type":"response.completed","response":{"id":"r9",'
                           b'"usage":{"input_tokens":7,"output_tokens":3}},'
                           b'"copilot_usage":{"total_nano_aiu":4473525000}}\n\n')
                return httpx.Response(200, content=rsse(),
                                      headers={"content-type": "text/event-stream"})
            output = [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]
            if rbody.get("tools"):
                output.append({"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"})
            return httpx.Response(200, json={"id": "r9", "status": "completed", "output": output,
                "usage": {"input_tokens": 7, "output_tokens": 3},
                "copilot_usage": {"total_nano_aiu": 4473525000}})
        if request.url.path == "/v1/messages":
            try:
                mbody = json.loads(request.content or b"{}")
            except Exception:
                mbody = {}
            if mbody.get("model") == "gemini-4-carbon":
                return httpx.Response(400, json={"error": {"code": "no_available_model_endpoints"}})
            return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]})
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


def test_anthropic_messages_forwarded(monkeypatch):
    client, calls = _make_client(monkeypatch)
    resp = client.post("/v1/messages", headers={**_auth(), "anthropic-version": "2023-06-01"},
                       json={"model": "claude-opus-5.5", "max_tokens": 16,
                             "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    sent = calls[-1]
    assert sent.url.path == "/v1/messages"
    assert sent.headers["Authorization"] == "Bearer gh-token"
    assert sent.headers.get("anthropic-version") == "2023-06-01"
    assert "x-api-key" not in sent.headers


def test_count_tokens_stub(monkeypatch):
    client, _ = _make_client(monkeypatch)
    resp = client.post("/v1/messages/count_tokens", headers=_auth(),
                       json={"messages": [{"role": "user", "content": "hello world"}]})
    assert resp.status_code == 200
    assert resp.json()["input_tokens"] >= 1


def test_adapted_responses_stream_translated_to_chat(monkeypatch):
    client, calls = _make_client(monkeypatch)
    adapt.set_cache({"gpt-6-luna": ["/responses"]})
    resp = client.post("/v1/chat/completions", headers=_auth(),
                       json={"model": "gpt-6-luna", "stream": True,
                             "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert b'"chat.completion.chunk"' in resp.content
    assert b'"content":"Hello"' in resp.content
    assert b'"total_nano_aiu"' in resp.content
    assert b"data: [DONE]\n\n" in resp.content
    assert calls[-1].url.path == "/responses"


def test_adapted_responses_non_stream_reencoded(monkeypatch):
    client, calls = _make_client(monkeypatch)
    adapt.set_cache({"gpt-6-luna": ["/responses"]})
    resp = client.post("/v1/chat/completions", headers=_auth(),
                       json={"model": "gpt-6-luna",
                             "tools": [{"type": "function", "function": {"name": "f"}}],
                             "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion" and data["id"] == "r9"
    assert data["choices"][0]["message"]["content"] == "Hello"
    assert data["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "f"
    assert data["choices"][0]["finish_reason"] == "tool_calls"
    assert data["usage"]["prompt_tokens"] == 7 and data["usage"]["total_tokens"] == 10
    assert data["copilot_usage"]["total_nano_aiu"] == 4473525000
    assert calls[-1].url.path == "/responses"


def test_chat_400_fallback_retries_via_responses(monkeypatch, arm_fallback):
    client, calls = _make_client(monkeypatch)
    adapt.set_cache({"mai-code-2-flash": ["/chat/completions", "/responses"]})
    resp = client.post("/v1/chat/completions", headers=_auth(),
                       json={"model": "mai-code-2-flash",
                             "tools": [{"type": "function", "function": {"name": "f"}}],
                             "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello"
    assert data["choices"][0]["finish_reason"] == "tool_calls"
    assert data["usage"]["prompt_tokens"] == 7 and data["usage"]["total_tokens"] == 10
    paths = [c.url.path for c in calls]
    assert paths == ["/chat/completions", "/responses"]  # exactly one retry, no recursion


def test_chat_400_fallback_streams_translated(monkeypatch, arm_fallback):
    client, calls = _make_client(monkeypatch)
    adapt.set_cache({"mai-code-2-flash": ["/chat/completions", "/responses"]})
    resp = client.post("/v1/chat/completions", headers=_auth(),
                       json={"model": "mai-code-2-flash", "stream": True,
                             "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert b'"chat.completion.chunk"' in resp.content
    assert b'"role":"assistant"' in resp.content
    assert b'"content":"Hello"' in resp.content
    assert b'"total_nano_aiu"' in resp.content
    assert b"data: [DONE]\n\n" in resp.content
    assert [c.url.path for c in calls] == ["/chat/completions", "/responses"]


def test_chat_400_passthrough_without_responses_advertised(monkeypatch, arm_fallback):
    client, calls = _make_client(monkeypatch)
    adapt.set_cache({"mai-code-2-flash": ["/chat/completions"]})
    resp = client.post("/v1/chat/completions", headers=_auth(),
                       json={"model": "mai-code-2-flash",
                             "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "no_available_model_endpoints"
    assert [c.url.path for c in calls] == ["/chat/completions"]  # no retry


def test_messages_adapted_to_responses_non_stream(monkeypatch):
    client, calls = _make_client(monkeypatch)
    adapt.set_cache({"gpt-6-luna": ["/responses"]})
    resp = client.post("/v1/messages", headers=_auth(),
                       json={"model": "gpt-6-luna", "max_tokens": 16,
                             "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "message" and data["role"] == "assistant"
    assert data["content"][0]["type"] == "text" and data["content"][0]["text"] == "Hello"
    assert data["stop_reason"] == "end_turn"
    assert data["usage"]["input_tokens"] > 0
    assert calls[-1].url.path == "/responses"


def test_messages_adapted_to_responses_stream(monkeypatch):
    client, calls = _make_client(monkeypatch)
    adapt.set_cache({"gpt-6-luna": ["/responses"]})
    resp = client.post("/v1/messages", headers=_auth(),
                       json={"model": "gpt-6-luna", "stream": True, "max_tokens": 16,
                             "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert b"event: message_start" in resp.content
    assert b"event: content_block_delta" in resp.content
    assert b"event: message_delta" in resp.content and b'"stop_reason":"end_turn"' in resp.content
    assert b"event: message_stop" in resp.content
    assert b"data: [DONE]" not in resp.content  # Anthropic protocol has no DONE sentinel
    assert calls[-1].url.path == "/responses"


def test_messages_native_when_advertised(monkeypatch):
    client, calls = _make_client(monkeypatch)
    adapt.set_cache({"claude-haiku-5.5": ["/chat/completions", "/v1/messages"]})
    resp = client.post("/v1/messages", headers={**_auth(), "anthropic-version": "2023-06-01"},
                       json={"model": "claude-haiku-5.5", "max_tokens": 16,
                             "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    sent = calls[-1]
    assert sent.url.path == "/v1/messages"  # native upstream passthrough
    assert json.loads(sent.content)["model"] == "claude-haiku-5.5"


def test_messages_native_passthrough_for_chat_only_model(monkeypatch):
    client, calls = _make_client(monkeypatch)
    adapt.set_cache({"gemini-4-carbon": ["/chat/completions"]})
    resp = client.post("/v1/messages", headers=_auth(),
                       json={"model": "gemini-4-carbon", "max_tokens": 16,
                             "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 400  # Copilot error surfaces honestly, no retry
    assert resp.json()["error"]["code"] == "no_available_model_endpoints"
    assert [c.url.path for c in calls] == ["/v1/messages"]


def test_native_responses_stream_records_usage(monkeypatch):
    """Native /responses passthrough must meter the nested response.completed frame."""
    client, calls = _make_client(monkeypatch)
    resp = client.post("/v1/responses", headers=_auth(),
                       json={"model": "gpt-6-luna", "stream": True, "input": []})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert calls[-1].url.path == "/responses"  # native passthrough, not adapted
    t = usage.totals()
    assert t["requests"] >= 1
    assert t["input_tokens"] >= 7 and t["output_tokens"] >= 3
    assert abs(t["cost_usd"] - 0.04473525) < 1e-9


def test_per_key_routing_uses_pinned_connection(monkeypatch):
    client, calls = _make_client(monkeypatch)
    connections.save("alt", "alt-token", "https://alt.example.com")
    raw = keys.create("pinned")
    keys.set_connection("pinned", "alt")
    resp = client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {raw}"},
                       json={"model": "gpt-5"})
    assert resp.status_code == 200
    sent = calls[-1]
    assert sent.headers["Authorization"] == "Bearer alt-token"
    assert str(sent.url).startswith("https://alt.example.com/")
    # an unmapped key follows the active (first) account instead
    other = keys.create("loose")
    client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {other}"},
                json={"model": "gpt-5"})
    assert calls[-1].headers["Authorization"] == "Bearer gh-token"


def test_pinned_key_routing_uses_pinned_catalog(monkeypatch):
    """A pinned key adapts based on ITS account's catalog, not the active account's."""
    client, calls = _make_client(monkeypatch)
    connections.save("alt", "alt-token", "https://alt.example.com")
    routing.set_cache({"m": ["/chat/completions"]}, label="work")  # active says chat-only
    routing.set_cache({"m": ["/responses"]}, label="alt")          # pinned says responses-only
    raw = keys.create("pinned", connection="alt")
    resp = client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {raw}"},
                       json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert calls[-1].url.path == "/responses"  # adapted via the pinned catalog
    assert str(calls[-1].url).startswith("https://alt.example.com/")


def test_native_responses_long_stream_meters_terminal(monkeypatch):
    """Usage in the terminal frame survives even when the stream exceeds the tap buffer."""
    connections.save("work", "gh-token")
    pad = b"x" * (proxy._SSE_CAP + 1000)

    async def long_sse():
        yield b'data: {"type":"response.output_text.delta","delta":"' + pad + b'"}\n\n'
        yield (b'data: {"type":"response.completed","response":{"id":"r",'
               b'"usage":{"input_tokens":7,"output_tokens":3}},'
               b'"copilot_usage":{"total_nano_aiu":4473525000}}\n\n')

    def handler(request):
        return httpx.Response(200, content=long_sse(),
                              headers={"content-type": "text/event-stream"})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.githubcopilot.com")
    monkeypatch.setattr(proxy, "upstream", mock)
    client = TestClient(proxy.build_app())
    resp = client.post("/v1/responses", headers=_auth(),
                       json={"model": "gpt-6-luna", "stream": True, "input": []})
    assert resp.status_code == 200
    t = usage.totals()
    assert t["input_tokens"] >= 7 and t["output_tokens"] >= 3
    assert abs(t["cost_usd"] - 0.04473525) < 1e-9


def test_adapted_reencode_failure_preserves_headers(monkeypatch):
    """The exception path must still forward rate-limit/retry headers."""
    connections.save("work", "gh-token")

    def handler(request):
        return httpx.Response(200, json={"id": "r", "status": "completed", "output": []},
                              headers={"x-request-id": "req-123",
                                       "content-type": "application/json"})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.githubcopilot.com")
    monkeypatch.setattr(proxy, "upstream", mock)

    def boom(payload, model):
        raise ValueError("reencode failed")

    monkeypatch.setattr(adapt, "responses_to_chat_response", boom)
    routing.set_cache({"gpt-6-luna": ["/responses"]})
    client = TestClient(proxy.build_app())
    resp = client.post("/v1/chat/completions", headers=_auth(),
                       json={"model": "gpt-6-luna", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == "req-123"
