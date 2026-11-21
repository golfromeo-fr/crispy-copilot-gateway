"""Chat-completions -> Responses API adaptation for /responses-only Copilot models."""
from __future__ import annotations

import json
import time

MODELS_TTL = 600  # seconds before the supported_endpoints cache refreshes
FALLBACK_CODE = "no_available_model_endpoints"  # Copilot rejects an advertised endpoint

_cache: dict = {"endpoints": {}, "fetched_at": 0.0}  # model_id -> frozenset(endpoints)


def set_cache(endpoints: dict) -> None:
    """Test hook: inject model endpoints without network and mark the cache fresh."""
    _cache["endpoints"] = {mid: frozenset(eps or []) for mid, eps in endpoints.items()}
    _cache["fetched_at"] = time.time()


async def _ensure_cache() -> None:
    """Refresh the supported_endpoints cache when stale; failures fail open."""
    if time.time() - _cache["fetched_at"] <= MODELS_TTL:
        return
    from . import proxy  # circular-import safety
    try:
        models = await proxy.fetch_models()
        _cache["endpoints"] = {m.get("id"): frozenset(m.get("supported_endpoints") or [])
                               for m in models if isinstance(m, dict)}
    except Exception:
        pass  # fail open; retry after TTL
    _cache["fetched_at"] = time.time()


def model_endpoints(model: str | None) -> frozenset | None:
    """Cached endpoint set for the model WITHOUT refreshing; None when unknown."""
    return _cache["endpoints"].get(model or "")


async def upstream_kind(model: str | None) -> str:
    """'responses' only when the model lists /responses and not /chat/completions.

    Unknown models or fetch failures fail open to 'chat' (passthrough).
    """
    await _ensure_cache()
    eps = model_endpoints(model)
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


def messages_to_responses_body(body: dict) -> dict:
    """Translate an Anthropic /v1/messages body into a Responses API body."""
    out: dict = {"model": body.get("model")}
    if "stream" in body:
        out["stream"] = body["stream"]
    system = body.get("system")
    if isinstance(system, str):
        instructions = [system] if system else []
    elif isinstance(system, list):
        instructions = [b.get("text", "") for b in system
                        if isinstance(b, dict) and b.get("type") == "text"]
    else:
        instructions = []
    if instructions:
        out["instructions"] = "\n\n".join(instructions)
    input_items: list[dict] = []
    for m in body.get("messages") or []:
        role = m.get("role")
        if role == "user":
            pending: list[dict] = []
            for block in _anthropic_blocks(m.get("content")):
                if block.get("type") == "text":
                    pending.append({"type": "input_text", "text": block.get("text", "")})
                elif block.get("type") == "image":
                    src = block.get("source") or {}
                    pending.append({"type": "input_image", "image_url":
                                    f"data:{src.get('media_type', 'application/octet-stream')};"
                                    f"base64,{src.get('data', '')}"})
                elif block.get("type") == "tool_result":
                    if pending:
                        input_items.append({"role": "user", "content": pending})
                        pending = []
                    input_items.append({"type": "function_call_output",
                                        "call_id": block.get("tool_use_id"),
                                        "output": _text_of(block.get("content"))})
            if pending:
                input_items.append({"role": "user", "content": pending})
        elif role == "assistant":
            texts: list[str] = []
            for block in _anthropic_blocks(m.get("content")):
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    if texts:
                        input_items.append({"role": "assistant",
                                            "content": [{"type": "output_text", "text": "\n".join(texts)}]})
                        texts = []
                    inp = block.get("input")
                    input_items.append({"type": "function_call", "call_id": block.get("id"),
                                        "name": block.get("name"),
                                        "arguments": json.dumps(inp) if isinstance(inp, dict) else str(inp)})
            if texts:
                input_items.append({"role": "assistant",
                                    "content": [{"type": "output_text", "text": "\n".join(texts)}]})
    out["input"] = input_items
    if body.get("max_tokens") is not None:
        out["max_output_tokens"] = body["max_tokens"]
    for k in ("temperature", "top_p"):
        if k in body:
            out[k] = body[k]
    tools = []
    for t in body.get("tools") or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        item = {"type": "function", "name": t["name"]}
        if t.get("description"):
            item["description"] = t["description"]
        if t.get("input_schema"):
            item["parameters"] = t["input_schema"]
        tools.append(item)
    if tools:
        out["tools"] = tools
    choice = body.get("tool_choice")
    if isinstance(choice, str):
        out["tool_choice"] = "required" if choice == "any" else choice
    elif isinstance(choice, dict) and choice.get("type") == "tool" and choice.get("name"):
        out["tool_choice"] = {"type": "function", "name": choice["name"]}
    # stop_sequences / top_k / metadata have no Responses equivalent and are dropped.
    return out


def _anthropic_blocks(content) -> list[dict]:
    """Anthropic content as a block list; a bare string is one text block."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def responses_to_messages_response(resp: dict, model: str) -> dict:
    """Re-encode a Responses API payload as an Anthropic message."""
    texts: list[str] = []
    content: list[dict] = []
    for item in resp.get("output") or []:
        if item.get("type") == "message":
            texts += [p.get("text", "") for p in item.get("content") or []
                      if isinstance(p, dict) and p.get("type") == "output_text"]
        elif item.get("type") == "function_call":
            args = item.get("arguments")
            if isinstance(args, str):
                try:
                    inp = json.loads(args)
                except Exception:
                    inp = {}
            elif isinstance(args, dict):
                inp = args
            else:
                inp = {}
            content.append({"type": "tool_use", "id": item.get("call_id"),
                            "name": item.get("name"),
                            "input": inp if isinstance(inp, dict) else {}})
    if texts:
        content.insert(0, {"type": "text", "text": "\n".join(texts)})
    stop = "tool_use" if any(b["type"] == "tool_use" for b in content) \
        else ("max_tokens" if resp.get("status") == "incomplete" else "end_turn")
    u = resp.get("usage") or {}
    out = {"id": resp.get("id"), "type": "message", "role": "assistant", "model": model,
           "content": content, "stop_reason": stop, "stop_sequence": None,
           "usage": {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}}
    if resp.get("copilot_usage"):
        out["copilot_usage"] = resp["copilot_usage"]
    return out
