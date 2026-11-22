"""FastAPI gateway: per-harness key auth, transparent Copilot forwarding, usage metering."""
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import adapt, adapt_stream, connections, copilot, keys, routing, usage

upstream = httpx.AsyncClient(
    base_url=copilot.BASE_URL,
    timeout=httpx.Timeout(connect=30.0, read=None, write=None, pool=None))

_FWD_HEADERS = ("content-type", "retry-after")
_FWD_PREFIXES = ("x-ratelimit", "x-request-id", "x-github")
_SSE_CAP = 4_000_000  # rolling-tail metering buffer; terminal usage frames always survive


def _basic_key_name(header: str) -> str | None:
    """Key name when the HTTP Basic password part verifies (username ignored)."""
    if not header.startswith("Basic "):
        return None
    try:
        _, _, password = base64.b64decode(header[6:]).decode("utf-8", "replace").partition(":")
    except Exception:
        return None
    return keys.identify(password)


def _model_of(body: bytes) -> str | None:
    try:
        return json.loads(body).get("model")
    except Exception:
        return None


def _stream_of(body: bytes) -> bool:
    try:
        return bool(json.loads(body).get("stream"))
    except Exception:
        return False


def _is_fallback_error(content: bytes) -> bool:
    """Copilot advertises /chat/completions but rejects it at request time."""
    try:
        return (json.loads(content).get("error") or {}).get("code") == routing.FALLBACK_CODE
    except Exception:
        return False


def _forward_headers(resp: httpx.Response) -> dict:
    # Forward only safe metadata; never content-length/transfer-encoding/connection.
    return {k: v for k, v in resp.headers.items()
            if k.lower() in _FWD_HEADERS or k.lower().startswith(_FWD_PREFIXES)}


def _record(key_name, model, parsed: dict) -> None:
    if parsed:
        usage.record(key_name, model, parsed.get("input_tokens"),
                     parsed.get("output_tokens"), parsed.get("nano_aiu"))


async def fetch_models(conn: dict | None = None) -> list[dict]:
    """Copilot's /models payload for a connection (default: the active account)."""
    conn = conn if conn is not None else connections.active_connection()
    if conn is None:
        raise RuntimeError("no GitHub connection - connect a GitHub account in the UI at /ui")
    base = (conn.get("api_endpoint") or copilot.BASE_URL).rstrip("/")
    headers = copilot.upstream_headers(conn["github_token"])
    req = upstream.build_request("GET", base + "/models", headers=headers)
    resp = await upstream.send(req)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", []) if isinstance(data, dict) else data


async def _finish(resp, buf, key_name, model) -> None:
    try:
        _record(key_name, model, usage.parse_sse(bytes(buf)))
    except Exception:
        pass  # metering is best-effort and must never surface into the stream
    finally:
        await resp.aclose()


_DROP_HEADERS = ("host", "content-length", "connection", "keep-alive", "transfer-encoding",
                 "te", "upgrade", "authorization", "x-api-key")


def _overlay_client_headers(headers: dict, request: Request) -> None:
    """Preserve protocol headers the client sent (anthropic-version, OpenAI-Beta, ...);
    auth and hop-by-hop headers are dropped, Copilot's own are overlaid last."""
    for key, value in request.headers.items():
        if key.lower() not in _DROP_HEADERS:
            headers.setdefault(key, value)
    headers["Content-Type"] = request.headers.get("content-type", "application/json")


async def _forward(request: Request, method: str, path: str) -> Response:
    key_name = getattr(request.state, "proxy_key", None)
    conn = connections.for_key(key_name)
    if conn is None:
        raise HTTPException(503, "no GitHub connection - connect a GitHub account in the UI at /ui")
    github_token = conn["github_token"]
    base = (conn.get("api_endpoint") or copilot.BASE_URL).rstrip("/")
    body = await request.body()
    model = _model_of(body)
    if path == "/chat/completions" and await routing.upstream_kind(model, key_name) == "responses":
        return await _forward_adapted(request, key_name, model, _stream_of(body))
    headers = copilot.upstream_headers(github_token, vision=copilot.has_vision(body))
    _overlay_client_headers(headers, request)
    # Absolute url overrides the client base_url, routing to this connection's endpoint.
    req = upstream.build_request(method, base + path, content=body, headers=headers)
    resp = await upstream.send(req, stream=True)
    fwd = _forward_headers(resp)
    media_type = resp.headers.get("content-type")
    if "text/event-stream" in (media_type or ""):
        buf = bytearray()

        async def tap():
            async for chunk in resp.aiter_raw():
                buf.extend(chunk)
                if len(buf) > _SSE_CAP:
                    del buf[:-_SSE_CAP]  # keep the tail so terminal usage frames survive long streams
                yield chunk  # byte-identical passthrough; metering never alters the stream

        return StreamingResponse(tap(), status_code=resp.status_code, media_type=media_type,
                                 headers=fwd,
                                 background=BackgroundTask(_finish, resp, buf, key_name, model))
    content = await resp.aread()
    await resp.aclose()
    # Copilot sometimes advertises /chat/completions but rejects it at request time
    # (live: mai-code-2-flash, code no_available_model_endpoints); retry once via /responses.
    if (resp.status_code == 400 and path == "/chat/completions" and _is_fallback_error(content)
            and "/responses" in (routing.model_endpoints(model, key_name) or frozenset())):
        return await _forward_adapted(request, key_name, model, _stream_of(body))
    try:
        _record(key_name, model, usage.parse_usage(json.loads(content)))
    except Exception:
        pass
    return Response(content=content, status_code=resp.status_code, media_type=media_type, headers=fwd)


async def _forward_translated(request: Request, key_name: str | None, model: str | None,
                              stream_requested: bool, *, translate: Callable[[dict], dict],
                              reencode: Callable[[dict, str], dict],
                              sse_translate: Callable[..., AsyncIterator[bytes]]) -> Response:
    """Translate the ORIGINAL body to /responses, forward, re-encode the reply.

    Shared by the chat and Anthropic adapted routes. Never falls back again: a
    failure here surfaces to the client as-is.
    """
    conn = connections.for_key(key_name)
    if conn is None:
        raise HTTPException(503, "no GitHub connection - connect a GitHub account in the UI at /ui")
    base = (conn.get("api_endpoint") or copilot.BASE_URL).rstrip("/")
    body = await request.body()  # cached by Starlette; re-reading is safe
    payload = json.loads(body)
    payload["stream"] = bool(stream_requested)  # original stream flag, honored exactly
    body_out = json.dumps(translate(payload)).encode()
    headers = copilot.upstream_headers(conn["github_token"], vision=copilot.has_vision(body))
    _overlay_client_headers(headers, request)
    req = upstream.build_request("POST", base + "/responses", content=body_out, headers=headers)
    resp = await upstream.send(req, stream=True)
    fwd = _forward_headers(resp)
    media_type = resp.headers.get("content-type")
    if "text/event-stream" in (media_type or ""):
        # Translator consumes frames and meters usage itself; just close upstream after.
        return StreamingResponse(
            sse_translate(resp.aiter_raw(), key_name=key_name, model=model),
            status_code=resp.status_code, media_type=media_type, headers=fwd,
            background=BackgroundTask(resp.aclose))
    content = await resp.aread()
    await resp.aclose()
    try:
        parsed = json.loads(content)
        _record(key_name, model, usage.parse_usage(parsed))
        return JSONResponse(reencode(parsed, model or ""),
                            status_code=resp.status_code, headers=fwd)
    except Exception:
        return Response(content=content, status_code=resp.status_code, media_type=media_type,
                        headers=fwd)


async def _forward_adapted(request: Request, key_name: str | None,
                           model: str | None, stream_requested: bool) -> Response:
    """Chat-completions body -> /responses -> chat.completion reply."""
    return await _forward_translated(request, key_name, model, stream_requested,
                                     translate=adapt.chat_to_responses_body,
                                     reencode=adapt.responses_to_chat_response,
                                     sse_translate=adapt_stream.sse_to_chat)


async def _forward_messages(request: Request) -> Response:
    """Route /v1/messages: native passthrough, or adapt to /responses when advertised.

    The endpoint cache refreshes when stale (a fresh server process must decide
    correctly on its first request). A chat-only model passes through natively
    so Copilot's 400 surfaces honestly - no adapter exists for that direction.
    """
    body = await request.body()
    model = _model_of(body)
    key_name = getattr(request.state, "proxy_key", None)
    await routing.ensure_cache(key_name)
    eps = routing.model_endpoints(model, key_name)
    if eps is None or "/v1/messages" in eps:
        return await _forward(request, "POST", "/v1/messages")
    if "/responses" in eps:
        return await _forward_messages_adapted(request, key_name, model, _stream_of(body))
    return await _forward(request, "POST", "/v1/messages")


async def _forward_messages_adapted(request: Request, key_name: str | None,
                                    model: str | None, stream_requested: bool) -> Response:
    """Anthropic messages body -> /responses -> Anthropic message reply."""
    return await _forward_translated(request, key_name, model, stream_requested,
                                     translate=adapt.messages_to_responses_body,
                                     reencode=adapt.responses_to_messages_response,
                                     sse_translate=adapt_stream.sse_to_messages)


def build_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await upstream.aclose()

    app = FastAPI(title="crispy-copilot-gateway", lifespan=lifespan)

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        path = request.url.path
        if path == "/healthz":
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if path.startswith("/ui") or "socket.io" in path:
            name = _basic_key_name(auth)
            if name is None:
                return JSONResponse({"error": "invalid or missing proxy key"}, status_code=401,
                                    headers={"WWW-Authenticate": 'Basic realm="crispy-copilot-gateway"'})
        else:
            key = auth[7:] if auth.startswith("Bearer ") else request.headers.get("x-api-key", "")
            name = keys.identify(key) if key else None
            if name is None:
                return JSONResponse({"error": "invalid or missing proxy key"}, status_code=401)
        request.state.proxy_key = name
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.get("/v1/models")
    async def models(request: Request) -> Response:
        return await _forward(request, "GET", "/models")

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        return await _forward(request, "POST", "/chat/completions")

    @app.post("/v1/responses")
    async def responses_route(request: Request) -> Response:
        return await _forward(request, "POST", "/responses")

    @app.post("/v1/messages")
    async def messages_route(request: Request) -> Response:
        return await _forward_messages(request)

    @app.post("/v1/messages/count_tokens")
    async def count_tokens_route(request: Request) -> Response:
        # Copilot has no count_tokens; Anthropic clients tolerate a rough estimate.
        body = await request.body()
        try:
            size = len(json.dumps(json.loads(body).get("messages", [])))
        except Exception:
            size = len(body)
        return JSONResponse({"input_tokens": max(1, size // 4)})
    from . import ui as gateway_ui

    gateway_ui.mount(app)
    return app
