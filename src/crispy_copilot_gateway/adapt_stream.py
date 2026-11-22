"""SSE re-encoder: Copilot Responses frames -> OpenAI chat / Anthropic messages chunks."""
from __future__ import annotations

import json
import uuid

from . import usage

# Copilot streams dot-form event types (live-verified 2026-11-22); the underscore
# spellings are kept as tolerance for upstream variants and older tests.
_ARG_DELTA = ("response.function_call_arguments.delta",
              "response.function_call_arguments_delta")
_ARG_DONE = ("response.function_call_arguments.done",
             "response.function_call_arguments_done")
_TERMINAL = ("response.completed", "response.failed", "response.incomplete")
# Events that begin assistant output; the chat role delta is sent once, before the first.
_ROLE_TRIGGERS = ("response.output_item.added", "response.output_text.delta") + _ARG_DELTA + _ARG_DONE


async def _frames(source):
    """Yield parsed SSE event dicts from a byte source; skip noise, never raise."""
    buf = b""
    async for raw in source:
        buf += raw
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            line = line.strip(b"\r")
            if not line.startswith(b"data: "):
                continue
            payload = line[6:].strip()
            if payload == b"[DONE]":
                continue  # upstream terminator replaced by our own
            try:
                yield json.loads(payload)
            except Exception:
                continue  # malformed frame -> skip, never crash
    if buf:
        line = buf.strip(b"\r")
        if line.startswith(b"data: "):
            payload = line[6:].strip()
            if payload != b"[DONE]":
                try:
                    yield json.loads(payload)
                except Exception:
                    pass  # malformed trailing line -> skip, never crash


def _remainder(full: str, streamed: str) -> str:
    """Part of full not already streamed ("" when streamed isn't a prefix of full)."""
    return full[len(streamed):] if full.startswith(streamed) else ""


def _error_text(resp: dict, status: str) -> str:
    """Upstream failure/incomplete reason, with a generic fallback."""
    return ((resp.get("error") or {}).get("message")
            or (resp.get("incomplete_details") or {}).get("reason")
            or f"upstream response {status}")


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


def _chat_call_delta(call_state: dict, call_key, call_id, name,
                     piece: str) -> dict | None:
    """Advance per-call stream state; return the chat tool_calls delta payload.

    None when there is nothing new to send (repeat announce or empty piece).
    """
    first = call_key not in call_state
    st = call_state.setdefault(call_key, {"index": len(call_state), "streamed": ""})
    st["streamed"] += piece
    if not piece and not first:
        return None
    fn: dict = {"arguments": piece}
    if first and name:
        fn["name"] = name
    call: dict = {"index": st["index"], "type": "function", "function": fn}
    if first and call_id:
        call["id"] = call_id
    return {"tool_calls": [call]}


def _event(etype: str, data: dict) -> bytes:
    return b"event: " + etype.encode() + b"\ndata: " + json.dumps(data, separators=(",", ":")).encode() + b"\n\n"


def _input_json_event(idx: int, piece: str) -> bytes:
    return _event("content_block_delta", {"type": "content_block_delta", "index": idx,
        "delta": {"type": "input_json_delta", "partial_json": piece}})


def _flush_args(idx: int, full: str, streamed: dict) -> bytes | None:
    """Emit the not-yet-streamed tail of full as input_json_delta; None when nothing new."""
    piece = _remainder(full, streamed.get(idx, ""))
    if not piece:
        return None
    streamed[idx] = streamed.get(idx, "") + piece
    return _input_json_event(idx, piece)


async def sse_to_chat(source, key_name: str | None = None, model: str | None = None):
    """Yield chat-format SSE bytes; meters usage itself on response.completed."""
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    sent_role = False
    items: dict = {}       # item_id -> (call_id, name) from output_item.added
    call_state: dict = {}  # call_key -> {"index", "streamed"} per in-flight tool call
    saw_calls = False
    done = False
    async for ev in _frames(source):
        kind = ev.get("type") or ""
        if not sent_role and kind in _ROLE_TRIGGERS:
            sent_role = True
            yield _chunk(cid, {"role": "assistant", "content": ""})
        if kind == "response.created":
            cid = (ev.get("response") or {}).get("id") or cid
        elif kind == "response.output_item.added":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                items[item.get("id") or item.get("item_id")] = (item.get("call_id"), item.get("name"))
                saw_calls = True
        elif kind == "response.output_text.delta":
            yield _chunk(cid, {"content": ev.get("delta", "")})
        elif kind in _ARG_DELTA:
            call_id, name = items.get(ev.get("item_id"), (None, None))
            call_id = ev.get("call_id") or call_id
            name = ev.get("name") or name
            saw_calls = True
            call_key = call_id or ev.get("item_id")
            payload = _chat_call_delta(call_state, call_key, call_id, name,
                                       ev.get("delta", "") or "")
            if payload:
                yield _chunk(cid, payload)
        elif kind in _ARG_DONE:
            call_id, name = items.get(ev.get("item_id"), (None, None))
            call_id = ev.get("call_id") or call_id
            name = ev.get("name") or name
            saw_calls = True
            call_key = call_id or ev.get("item_id")
            st = call_state.get(call_key)
            piece = _remainder(ev.get("arguments") or "", (st or {}).get("streamed", ""))
            if piece:
                payload = _chat_call_delta(call_state, call_key, call_id, name, piece)
                if payload:
                    yield _chunk(cid, payload)
        elif kind == "response.output_item.done":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                call_id = item.get("call_id")
                name = item.get("name")
                saw_calls = True
                item_id = item.get("id") or ev.get("item_id")
                call_key = call_id or item_id
                st = call_state.get(call_key) or call_state.get(item_id)
                if st is not None and call_key not in call_state:
                    call_key = item_id  # deltas were keyed under the item id
                piece = _remainder(item.get("arguments") or "", (st or {}).get("streamed", ""))
                if piece:
                    payload = _chat_call_delta(call_state, call_key, call_id, name, piece)
                    if payload:
                        yield _chunk(cid, payload)
        elif kind in _TERMINAL:
            resp = ev.get("response") or {}
            cid = resp.get("id") or cid
            usage_, _ = _usage_of(resp)
            copilot = ev.get("copilot_usage") or resp.get("copilot_usage")
            if kind != "response.completed":
                status = kind.split(".")[-1]
                yield _chunk(cid, {"content": f"[{status}] {_error_text(resp, status)}"})
            if usage_:
                try:
                    usage.record(key_name, model, usage_["prompt_tokens"],
                                 usage_["completion_tokens"],
                                 (copilot or {}).get("total_nano_aiu"))
                except Exception:
                    pass  # metering is best-effort
            finish = "tool_calls" if saw_calls else (
                "length" if kind == "response.incomplete" else "stop")
            yield _chunk(cid, {}, finish, usage_, copilot)
            yield b"data: [DONE]\n\n"
            done = True
            break
    if not done:
        yield b"data: [DONE]\n\n"  # upstream closed without a terminal frame


async def sse_to_messages(source, key_name: str | None = None, model: str | None = None):
    """Yield Anthropic-format SSE (event+data pairs); meters usage on response.completed."""
    cid = "msg_" + uuid.uuid4().hex[:24]
    items: dict = {}  # item_id -> assigned content block index
    open_blocks: set = set()
    streamed: dict = {}  # block index -> arguments already sent
    next_index = 0  # content blocks (text and tool_use) get unique sequential indices
    last_text_idx: int | None = None
    saw_calls = False
    input_tokens = 0
    async for ev in _frames(source):
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
            itype = item.get("type")
            if itype in ("function_call", "message"):
                idx = next_index
                next_index += 1
                items[item.get("id") or item.get("item_id")] = idx
                open_blocks.add(idx)
                if itype == "function_call":
                    saw_calls = True
                    yield _event("content_block_start", {"type": "content_block_start", "index": idx,
                        "content_block": {"type": "tool_use", "id": item.get("call_id"),
                                          "name": item.get("name")}})
                else:
                    last_text_idx = idx
                    yield _event("content_block_start", {"type": "content_block_start", "index": idx,
                        "content_block": {"type": "text", "text": ""}})
        elif kind == "response.output_text.delta":
            idx = items.get(ev.get("item_id")) if ev.get("item_id") else None
            if idx is None:
                # no item id: continue the open text block, else start a fresh one
                if last_text_idx is not None and last_text_idx in open_blocks:
                    idx = last_text_idx
                else:
                    idx = next_index
                    next_index += 1
                    last_text_idx = idx
                    open_blocks.add(idx)
                    yield _event("content_block_start", {"type": "content_block_start", "index": idx,
                        "content_block": {"type": "text", "text": ""}})
            else:
                last_text_idx = idx
                if idx not in open_blocks:
                    open_blocks.add(idx)
                    yield _event("content_block_start", {"type": "content_block_start", "index": idx,
                        "content_block": {"type": "text", "text": ""}})
            yield _event("content_block_delta", {"type": "content_block_delta", "index": idx,
                "delta": {"type": "text_delta", "text": ev.get("delta", "")}})
        elif kind in _ARG_DELTA:
            idx = items.get(ev.get("item_id"))
            piece = ev.get("delta", "") or ""
            if idx is not None and piece:
                streamed[idx] = streamed.get(idx, "") + piece
                yield _input_json_event(idx, piece)
        elif kind in _ARG_DONE:
            idx = items.get(ev.get("item_id"))
            if idx is not None:
                ev_bytes = _flush_args(idx, ev.get("arguments") or "", streamed)
                if ev_bytes:
                    yield ev_bytes
        elif kind in ("response.output_item.done", "content_block_stop"):
            item = ev.get("item") or {}
            if kind == "response.output_item.done":
                idx = items.get(item.get("id") or ev.get("item_id"))
                if item.get("type") == "function_call":
                    saw_calls = True
                    if idx is None:
                        # upstream skipped output_item.added - open the block now
                        idx = next_index
                        next_index += 1
                        items[item.get("id") or ev.get("item_id")] = idx
                        open_blocks.add(idx)
                        yield _event("content_block_start", {"type": "content_block_start",
                            "index": idx,
                            "content_block": {"type": "tool_use", "id": item.get("call_id"),
                                              "name": item.get("name")}})
                    ev_bytes = _flush_args(idx, item.get("arguments") or "", streamed)
                    if ev_bytes:
                        yield ev_bytes
            else:
                idx = ev.get("index")
            if isinstance(idx, int) and idx in open_blocks:
                open_blocks.discard(idx)
                yield _event("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif kind in _TERMINAL:
            cid = resp.get("id") or cid
            u = resp.get("usage") or ev.get("usage") or {}
            in_toks = u.get("input_tokens") or input_tokens or 0
            out_toks = u.get("output_tokens") or 0
            copilot = ev.get("copilot_usage") or resp.get("copilot_usage")
            if kind != "response.completed":
                idx = next_index
                next_index += 1
                open_blocks.add(idx)
                yield _event("content_block_start", {"type": "content_block_start", "index": idx,
                    "content_block": {"type": "text", "text": ""}})
                status = kind.split(".")[-1]
                yield _event("content_block_delta", {"type": "content_block_delta", "index": idx,
                    "delta": {"type": "text_delta", "text": f"[{status}] {_error_text(resp, status)}"}})
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
            break
    # Anthropic protocol ends at message_stop; no [DONE] sentinel is emitted.
