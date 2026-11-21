"""FastAPI gateway: per-harness key auth, transparent Copilot forwarding, usage metering."""
from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import adapt, adapt_stream, connections, copilot, keys, usage

upstream = httpx.AsyncClient(
    base_url=copilot.BASE_URL,
    timeout=httpx.Timeout(connect=30.0, read=None, write=None, pool=None))

_FWD_HEADERS = ("content-type", "retry-after")
_FWD_PREFIXES = ("x-ratelimit", "x-request-id", "x-github")
_SSE_CAP = 4_000_000


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


def _forward_headers(resp: httpx.Response) -> dict:
    # Forward only safe metadata; never content-length/transfer-encoding/connection.
    return {k: v for k, v in resp.headers.items()
            if k.lower() in _FWD_HEADERS or k.lower().startswith(_FWD_PREFIXES)}


def _record(key_name, model, parsed: dict) -> None:
    if parsed:
        usage.record(key_name, model, parsed.get("input_tokens"),
                     parsed.get("output_tokens"), parsed.get("nano_aiu"))


async def fetch_models() -> list[dict]:
    """Copilot's /models payload via the active connection (token + endpoint)."""
    conn = connections.active_connection()
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


async def _forward(request: Request, method: str, path: str) -> Response:
    conn = connections.active_connection()
    if conn is None:
        raise HTTPException(503, "no GitHub connection - connect a GitHub account in the UI at /ui")
    github_token = conn["github_token"]
    base = (conn.get("api_endpoint") or copilot.BASE_URL).rstrip("/")
    body = await request.body()
    model = _model_of(body)
    upstream_path, body_out, adapted = path, body, False
    if path == "/chat/completions" and await adapt.upstream_kind(model) == "responses":
        try:
            body_out = json.dumps(adapt.chat_to_responses_body(json.loads(body))).encode()
            upstream_path, adapted = "/responses", True
        except Exception:
            pass  # malformed chat body -> fail-open passthrough
    headers = copilot.upstream_headers(github_token, vision=copilot.has_vision(body))
    # Preserve protocol headers the client sent (anthropic-version, OpenAI-Beta, ...);
    # auth and hop-by-hop headers are dropped, Copilot's own are overlaid last.
    for key, value in request.headers.items():
        if key.lower() not in _DROP_HEADERS:
            headers.setdefault(key, value)
    headers["Content-Type"] = request.headers.get("content-type", "application/json")
    # TODO (P4): per-key -> connection mapping; every key currently uses the first connection.
    # Absolute url overrides the client base_url, routing to this connection's endpoint.
    req = upstream.build_request(method, base + upstream_path, content=body_out, headers=headers)
    resp = await upstream.send(req, stream=True)
    fwd = _forward_headers(resp)
    media_type = resp.headers.get("content-type")
    key_name = getattr(request.state, "proxy_key", None)
    if "text/event-stream" in (media_type or ""):
        if adapted:
            # Translator consumes frames and meters usage itself; just close upstream after.
            return StreamingResponse(
                adapt_stream.sse_to_chat(resp.aiter_raw(), key_name=key_name, model=model),
                status_code=resp.status_code, media_type=media_type, headers=fwd,
                background=BackgroundTask(resp.aclose))
        buf = bytearray()

        async def tap():
            async for chunk in resp.aiter_raw():
                if len(buf) < _SSE_CAP:
                    buf.extend(chunk[:_SSE_CAP - len(buf)])
                yield chunk  # byte-identical passthrough; metering never alters the stream

        return StreamingResponse(tap(), status_code=resp.status_code, media_type=media_type,
                                 headers=fwd,
                                 background=BackgroundTask(_finish, resp, buf, key_name, model))
    content = await resp.aread()
    await resp.aclose()
    if adapted:
        try:
            payload = json.loads(content)
            _record(key_name, model, usage.parse_usage(payload))
            chat = adapt.responses_to_chat_response(payload, model or "")
        except Exception:
            return Response(content=content, status_code=resp.status_code, media_type=media_type)
        return JSONResponse(chat, status_code=resp.status_code, headers=fwd)
    try:
        _record(key_name, model, usage.parse_usage(json.loads(content)))
    except Exception:
        pass
    return Response(content=content, status_code=resp.status_code, media_type=media_type, headers=fwd)


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
        return await _forward(request, "POST", "/v1/messages")

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
