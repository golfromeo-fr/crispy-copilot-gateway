"""SSE re-encoder: Copilot Responses frames -> OpenAI chat-completions chunks."""
from __future__ import annotations

import json
import uuid

from . import usage


def _chunk(cid: str, delta: dict, finish: str | None = None,
           usage_: dict | None = None, copilot: dict | None = None) -> bytes:
    choice: dict = {"index": 0, "delta": delta}
    if finish:
        choice["finish_reason"] = finish
    obj: dict = {"id": cid, "object": "chat.completion.chunk", "choices": [choice]}
    if usage_ is not None:
        obj["usage"] = usage_
    if copilot is not None:
        obj["copilot_usage"] = copilot
    return b"data: " + json.dumps(obj, separators=(",", ":")).encode() + b"\n\n"


def _usage_of(resp: dict) -> tuple[dict | None, dict | None]:
    u = resp.get("usage") or {}
    if not u:
        return None, resp.get("copilot_usage")
    return ({"prompt_tokens": u.get("input_tokens"),
             "completion_tokens": u.get("output_tokens"),
             "total_tokens": (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)},
            resp.get("copilot_usage"))


async def sse_to_chat(source, key_name: str | None = None, model: str | None = None):
    """Yield chat-format SSE bytes; meters usage itself on response.completed."""
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    buf = b""
    sent_role = False
    items: dict = {}  # item_id -> (call_id, name) from output_item.added
    announced: set = set()
    saw_calls = False
    done = False
    async for raw in source:
        buf += raw
        while b"\n" in buf and not done:
            line, _, buf = buf.partition(b"\n")
            line = line.strip(b"\r")
            if not line.startswith(b"data: "):
                continue
            payload = line[6:].strip()
            if payload == b"[DONE]":
                continue  # upstream terminator replaced by our own
            try:
                ev = json.loads(payload)
            except Exception:
                continue  # malformed frame -> skip, never crash
            kind = ev.get("type") or ""
            if kind == "response.created":
                cid = (ev.get("response") or {}).get("id") or cid
            elif kind == "response.output_item.added":
                item = ev.get("item") or {}
                if item.get("type") == "function_call":
                    items[item.get("id") or item.get("item_id")] = (item.get("call_id"), item.get("name"))
                if item.get("type") == "message" and not sent_role:
                    sent_role = True
                    yield _chunk(cid, {"role": "assistant", "content": ""})
            elif kind == "response.output_text.delta":
                yield _chunk(cid, {"content": ev.get("delta", "")})
            elif kind == "response.function_call_arguments_delta":
                call_id, name = items.get(ev.get("item_id"), (None, None))
                call_id = ev.get("call_id") or call_id
                name = ev.get("name") or name
                saw_calls = True
                call_key = call_id or ev.get("item_id")
                first = call_key not in announced
                if first:
                    announced.add(call_key)
                fn: dict = {"arguments": ev.get("delta", "")}
                if first and name:
                    fn["name"] = name
                call: dict = {"index": 0, "type": "function", "function": fn}
                if first and call_id:
                    call["id"] = call_id
                yield _chunk(cid, {"tool_calls": [call]})
            elif kind in ("response.completed", "response.failed", "response.incomplete"):
                resp = ev.get("response") or {}
                cid = resp.get("id") or cid
                usage_, copilot = _usage_of(resp)
                if kind == "response.completed":
                    if usage_:
                        try:
                            usage.record(key_name, model, usage_["prompt_tokens"],
                                         usage_["completion_tokens"],
                                         (copilot or {}).get("total_nano_aiu"))
                        except Exception:
                            pass  # metering is best-effort
                    yield _chunk(cid, {}, "tool_calls" if saw_calls else "stop", usage_, copilot)
                else:
                    status = kind.split(".")[-1]
                    err = ((resp.get("error") or {}).get("message")
                           or (resp.get("incomplete_details") or {}).get("reason")
                           or f"upstream response {status}")
                    yield _chunk(cid, {"content": f"[{status}] {err}"})
                    yield _chunk(cid, {}, "stop", usage_, copilot)
                yield b"data: [DONE]\n\n"
                done = True
    if not done:
        yield b"data: [DONE]\n\n"  # upstream closed without a terminal frame


def _event(etype: str, data: dict) -> bytes:
    return b"event: " + etype.encode() + b"\ndata: " + json.dumps(data, separators=(",", ":")).encode() + b"\n\n"


async def sse_to_messages(source, key_name: str | None = None, model: str | None = None):
    """Yield Anthropic-format SSE (event+data pairs); meters usage on response.completed."""
    cid = "msg_" + uuid.uuid4().hex[:24]
    buf = b""
    items: dict = {}  # item_id -> assigned content block index
    open_blocks: set = set()
    next_call_index = 1  # text block is always index 0; function calls increment from 1
    saw_calls = False
    input_tokens = 0
    done = False
    async for raw in source:
        buf += raw
        while b"\n" in buf and not done:
            line, _, buf = buf.partition(b"\n")
            line = line.strip(b"\r")
            if not line.startswith(b"data: "):
                continue
            try:
                ev = json.loads(line[6:].strip())
            except Exception:
                continue  # malformed frame -> skip, never crash
            kind = ev.get("type") or ""
            resp = ev.get("response") or {}
            if kind == "response.created":
                cid = resp.get("id") or cid
                input_tokens = (resp.get("usage") or {}).get("input_tokens") or 0
                yield _event("message_start", {"type": "message_start", "message": {
                    "id": cid, "role": "assistant", "model": resp.get("model") or model,
                    "content": [], "usage": {"input_tokens": input_tokens}}})
            elif kind == "response.output_item.added":
                item = ev.get("item") or {}
                if item.get("type") == "function_call":
                    idx = next_call_index
                    next_call_index += 1
                    items[item.get("id") or item.get("item_id")] = idx
                    open_blocks.add(idx)
                    saw_calls = True
                    yield _event("content_block_start", {"type": "content_block_start", "index": idx,
                        "content_block": {"type": "tool_use", "id": item.get("call_id"),
                                          "name": item.get("name")}})
            elif kind == "response.output_text.delta":
                if 0 not in open_blocks:
                    open_blocks.add(0)
                    yield _event("content_block_start", {"type": "content_block_start", "index": 0,
                        "content_block": {"type": "text", "text": ""}})
                if ev.get("item_id"):
                    items[ev.get("item_id")] = 0
                yield _event("content_block_delta", {"type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": ev.get("delta", "")}})
            elif kind == "response.function_call_arguments_delta":
                idx = items.get(ev.get("item_id"))
                if idx is not None:
                    yield _event("content_block_delta", {"type": "content_block_delta", "index": idx,
                        "delta": {"type": "input_json_delta", "partial_json": ev.get("delta", "")}})
            elif kind in ("response.output_item.done", "content_block_stop"):
                if kind == "response.output_item.done":
                    idx = items.get((ev.get("item") or {}).get("id") or ev.get("item_id"))
                else:
                    idx = ev.get("index")
                if isinstance(idx, int) and idx in open_blocks:
                    open_blocks.discard(idx)
                    yield _event("content_block_stop", {"type": "content_block_stop", "index": idx})
            elif kind in ("response.completed", "response.failed", "response.incomplete"):
                cid = resp.get("id") or cid
                u = resp.get("usage") or {}
                in_toks = u.get("input_tokens") or input_tokens or 0
                out_toks = u.get("output_tokens") or 0
                copilot = resp.get("copilot_usage")
                if kind != "response.completed":
                    if 0 not in open_blocks:
                        open_blocks.add(0)
                        yield _event("content_block_start", {"type": "content_block_start", "index": 0,
                            "content_block": {"type": "text", "text": ""}})
                    status = kind.split(".")[-1]
                    err = ((resp.get("error") or {}).get("message")
                           or (resp.get("incomplete_details") or {}).get("reason")
                           or f"upstream response {status}")
                    yield _event("content_block_delta", {"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": f"[{status}] {err}"}})
                for idx in sorted(open_blocks):
                    yield _event("content_block_stop", {"type": "content_block_stop", "index": idx})
                open_blocks.clear()
                stop = ("tool_use" if saw_calls else
                        ("max_tokens" if resp.get("status") == "incomplete" else "end_turn"))
                yield _event("message_delta", {"type": "message_delta",
                    "delta": {"stop_reason": stop}, "usage": {"output_tokens": out_toks}})
                try:
                    usage.record(key_name, model, in_toks, out_toks, (copilot or {}).get("total_nano_aiu"))
                except Exception:
                    pass  # metering is best-effort
                yield _event("message_stop", {"type": "message_stop"})
                done = True
    # Anthropic protocol ends at message_stop; no [DONE] sentinel is emitted.
