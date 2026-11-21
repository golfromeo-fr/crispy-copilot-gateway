"""FastAPI gateway: per-harness key auth, transparent Copilot forwarding, usage metering."""
from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import connections, copilot, keys, usage

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


async def _finish(resp, buf, key_name, model) -> None:
    try:
        _record(key_name, model, usage.parse_sse(bytes(buf)))
    except Exception:
        pass  # metering is best-effort and must never surface into the stream
    finally:
        await resp.aclose()


async def _forward(request: Request, method: str, path: str) -> Response:
    conn = connections.active_connection()
    if conn is None:
        raise HTTPException(503, "no GitHub connection - connect a GitHub account in the UI at /ui")
    github_token = conn["github_token"]
    base = (conn.get("api_endpoint") or copilot.BASE_URL).rstrip("/")
    body = await request.body()
    headers = copilot.upstream_headers(github_token, vision=copilot.has_vision(body))
    headers["Content-Type"] = request.headers.get("content-type", "application/json")
    # TODO (P4): per-key -> connection mapping; every key currently uses the first connection.
    # Absolute url overrides the client base_url, routing to this connection's endpoint.
    req = upstream.build_request(method, base + path, content=body, headers=headers)
    resp = await upstream.send(req, stream=True)
    fwd = _forward_headers(resp)
    media_type = resp.headers.get("content-type")
    key_name = getattr(request.state, "proxy_key", None)
    model = _model_of(body)
    if "text/event-stream" in (media_type or ""):
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

    # TODO (P2): /v1/messages (Anthropic) route on Copilot is TBD in live testing - not routed yet.
    from . import ui as gateway_ui

    gateway_ui.mount(app)
    return app
