"""FastAPI gateway: per-harness key auth + transparent forwarding to the Copilot upstream."""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import connections, copilot, keys

upstream = httpx.AsyncClient(base_url=copilot.BASE_URL, timeout=None)


def _basic_key_ok(header: str) -> bool:
    """True when the HTTP Basic password part verifies as a proxy key (username ignored)."""
    try:
        _, _, password = base64.b64decode(header[6:]).decode("utf-8", "replace").partition(":")
    except Exception:
        return False
    return header.startswith("Basic ") and keys.verify(password)


async def _forward(request: Request, method: str, path: str) -> Response:
    body = await request.body()
    github_token = connections.first_token()
    if github_token is None:
        raise HTTPException(503, "no GitHub connection - run crispy-copilot login <label>")
    copilot_token = await copilot.get_copilot_token(github_token, upstream)
    headers = copilot.upstream_headers(copilot_token)
    headers["Content-Type"] = request.headers.get("content-type", "application/json")
    # TODO (P4): multi-account (per-key -> connection mapping); every key currently uses first_token().
    req = upstream.build_request(method, path, content=body, headers=headers)
    resp = await upstream.send(req, stream=True)
    media_type = resp.headers.get("content-type")
    if "text/event-stream" in (media_type or ""):
        return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, media_type=media_type,
                                 background=BackgroundTask(resp.aclose))
    content = await resp.aread()
    await resp.aclose()
    return Response(content=content, status_code=resp.status_code, media_type=media_type)


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
            # Browser gate: HTTP Basic whose password part must verify as a proxy key.
            if not _basic_key_ok(auth):
                return JSONResponse({"error": "invalid or missing proxy key"}, status_code=401,
                                    headers={"WWW-Authenticate": 'Basic realm="crispy-copilot-gateway"'})
        else:
            key = auth[7:] if auth.startswith("Bearer ") else request.headers.get("x-api-key", "")
            if not key or not keys.verify(key):
                return JSONResponse({"error": "invalid or missing proxy key"}, status_code=401)
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
