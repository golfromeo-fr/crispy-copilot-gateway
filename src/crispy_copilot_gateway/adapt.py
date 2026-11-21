"""Chat-completions -> Responses API adaptation for /responses-only Copilot models."""
from __future__ import annotations

import json
import time

MODELS_TTL = 600  # seconds before the supported_endpoints cache refreshes

_cache: dict = {"endpoints": {}, "fetched_at": 0.0}  # model_id -> frozenset(endpoints)


def set_cache(endpoints: dict) -> None:
    """Test hook: inject model endpoints without network and mark the cache fresh."""
    _cache["endpoints"] = {mid: frozenset(eps or []) for mid, eps in endpoints.items()}
    _cache["fetched_at"] = time.time()


async def upstream_kind(model: str | None) -> str:
    """'responses' only when the model lists /responses and not /chat/completions.

    Unknown models or fetch failures fail open to 'chat' (passthrough).
    """
    if time.time() - _cache["fetched_at"] > MODELS_TTL:
        from . import proxy  # circular-import safety
        try:
            models = await proxy.fetch_models()
            _cache["endpoints"] = {m.get("id"): frozenset(m.get("supported_endpoints") or [])
                                   for m in models if isinstance(m, dict)}
            _cache["fetched_at"] = time.time()
        except Exception:
            _cache["fetched_at"] = time.time()  # fail open; retry after TTL
    eps = _cache["endpoints"].get(model or "")
    if eps is not None and "/responses" in eps and "/chat/completions" not in eps:
        return "responses"
    return "chat"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") in ("text", "output_text"))
    return ""


def _user_parts(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    parts: list[dict] = []
    for p in content or []:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            parts.append({"type": "input_text", "text": p.get("text", "")})
        elif p.get("type") == "image_url":
            url = p.get("image_url")
            url = url.get("url") if isinstance(url, dict) else url
            parts.append({"type": "input_image", "image_url": url})
    return parts


def chat_to_responses_body(body: dict) -> dict:
    """Translate an OpenAI chat-completions body into a Responses API body."""
    out: dict = {"model": body.get("model")}
    if "stream" in body:
        out["stream"] = body["stream"]
    instructions: list[str] = []
    input_items: list[dict] = []
    for m in body.get("messages") or []:
        role, content = m.get("role"), m.get("content")
        if role in ("system", "developer"):
            text = _text_of(content)
            if text:
                instructions.append(text)
        elif role == "user":
            input_items.append({"role": "user", "content": _user_parts(content)})
        elif role == "assistant":
            text = _text_of(content)
            if text:
                input_items.append({"role": "assistant",
                                    "content": [{"type": "output_text", "text": text}]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                input_items.append({"type": "function_call", "call_id": tc.get("id"),
                                    "name": fn.get("name"), "arguments": fn.get("arguments")})
        elif role == "tool":
            input_items.append({"type": "function_call_output", "call_id": m.get("tool_call_id"),
                                "output": _text_of(content)})
    if instructions:
        out["instructions"] = "\n\n".join(instructions)
    out["input"] = input_items
    max_toks = body.get("max_completion_tokens") or body.get("max_tokens")
    if max_toks is not None:
        out["max_output_tokens"] = max_toks
    for k in ("temperature", "top_p"):
        if k in body:
            out[k] = body[k]
    tools = []
    for t in body.get("tools") or []:
        fn = t.get("function") or {}
        if t.get("type") == "function" and fn.get("name"):
            item = {"type": "function", "name": fn["name"]}
            for k in ("description", "parameters"):
                if fn.get(k):
                    item[k] = fn[k]
            tools.append(item)
    if tools:
        out["tools"] = tools
    choice = body.get("tool_choice")
    if isinstance(choice, str):
        out["tool_choice"] = choice
    elif isinstance(choice, dict) and (choice.get("function") or {}).get("name"):
        out["tool_choice"] = {"type": "function", "name": choice["function"]["name"]}
    if isinstance(body.get("response_format"), dict):
        out["text"] = {"format": body["response_format"]}
    return out


def responses_to_chat_response(resp: dict, model: str) -> dict:
    """Re-encode a Responses API payload as an OpenAI chat.completion."""
    texts: list[str] = []
    tool_calls = []
    for item in resp.get("output") or []:
        if item.get("type") == "message":
            texts += [p.get("text", "") for p in item.get("content") or []
                      if isinstance(p, dict) and p.get("type") == "output_text"]
        elif item.get("type") == "function_call":
            tool_calls.append({"id": item.get("call_id"), "type": "function",
                               "function": {"name": item.get("name"),
                                            "arguments": item.get("arguments")}})
    message: dict = {"role": "assistant", "content": "\n".join(texts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    finish = "tool_calls" if tool_calls else ("length" if resp.get("status") == "incomplete"
                                              else "stop")
    u = resp.get("usage") or {}
    out = {"id": resp.get("id"), "object": "chat.completion", "created": int(time.time()),
           "model": model,
           "choices": [{"index": 0, "message": message, "finish_reason": finish}],
           "usage": {"prompt_tokens": u.get("input_tokens"),
                     "completion_tokens": u.get("output_tokens"),
                     "total_tokens": (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)}}
    if resp.get("copilot_usage"):
        out["copilot_usage"] = resp["copilot_usage"]
    return out
