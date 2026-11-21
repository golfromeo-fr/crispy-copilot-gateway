"""Protocol adaptation: chat<->Responses bodies/frames, no network (set_cache)."""
import asyncio
import json

from crispy_copilot_gateway import adapt, usage
from crispy_copilot_gateway.adapt_stream import sse_to_chat


def _chat_body() -> dict:
    return {
        "model": "gpt-6-luna", "stream": True,
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "developer", "content": "no fluff"},
            {"role": "user", "content": "hi"},
            {"role": "user", "content": [{"type": "text", "text": "look"},
                                          {"type": "image_url", "image_url": {"url": "http://x"}}]},
            {"role": "assistant", "content": "sure"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ],
        "max_tokens": 5, "max_completion_tokens": 9,
        "temperature": 0.5, "top_p": 0.9,
        "tools": [{"type": "function", "function": {"name": "f", "description": "d",
                                                    "parameters": {"a": 1}}}],
        "tool_choice": {"type": "function", "function": {"name": "f"}},
        "response_format": {"type": "json_object"},
        "stop": "\n", "presence_penalty": 1, "frequency_penalty": 1,
        "logprobs": True, "n": 2, "stream_options": {},
    }


def test_chat_to_responses_body_translation():
    out = adapt.chat_to_responses_body(_chat_body())
    assert out["model"] == "gpt-6-luna" and out["stream"] is True
    assert out["instructions"] == "be brief\n\nno fluff"
    assert out["input"][0] == {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    assert out["input"][1]["content"] == [{"type": "input_text", "text": "look"},
                                          {"type": "input_image", "image_url": "http://x"}]
    assert out["input"][2] == {"role": "assistant",
                               "content": [{"type": "output_text", "text": "sure"}]}
    assert out["input"][3] == {"type": "function_call", "call_id": "c1",
                               "name": "f", "arguments": "{}"}
    assert out["input"][4] == {"type": "function_call_output", "call_id": "c1", "output": "result"}
    assert out["max_output_tokens"] == 9
    assert out["temperature"] == 0.5 and out["top_p"] == 0.9
    assert out["tools"] == [{"type": "function", "name": "f", "description": "d",
                             "parameters": {"a": 1}}]
    assert out["tool_choice"] == {"type": "function", "name": "f"}
    assert out["text"] == {"format": {"type": "json_object"}}
    for dropped in ("stop", "presence_penalty", "frequency_penalty", "logprobs", "n",
                    "stream_options"):
        assert dropped not in out


def test_tool_choice_string_passthrough():
    assert adapt.chat_to_responses_body({"messages": [], "tool_choice": "auto"})["tool_choice"] == "auto"
    assert adapt.chat_to_responses_body({"messages": [], "tool_choice": "none"})["tool_choice"] == "none"


def test_responses_to_chat_response_text_tools_usage():
    resp = {"id": "r1", "status": "completed", "output": [
        {"type": "reasoning"},
        {"type": "message", "content": [{"type": "output_text", "text": "alpha"},
                                         {"type": "output_text", "text": "beta"}]},
        {"type": "function_call", "call_id": "c9", "name": "f", "arguments": "{}"}],
        "usage": {"input_tokens": 4, "output_tokens": 2},
        "copilot_usage": {"total_nano_aiu": 555}}
    out = adapt.responses_to_chat_response(resp, "gpt-6-luna")
    assert out["object"] == "chat.completion" and out["id"] == "r1" and out["model"] == "gpt-6-luna"
    msg = out["choices"][0]["message"]
    assert msg["content"] == "alpha\nbeta"
    assert msg["tool_calls"] == [{"id": "c9", "type": "function",
                                  "function": {"name": "f", "arguments": "{}"}}]
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert out["usage"] == {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
    assert out["copilot_usage"] == {"total_nano_aiu": 555}


def test_finish_reason_length_when_incomplete():
    out = adapt.responses_to_chat_response({"status": "incomplete", "output": [], "usage": {}}, "m")
    assert out["choices"][0]["finish_reason"] == "length"
    assert "tool_calls" not in out["choices"][0]["message"]


def test_upstream_kind_from_injected_cache():
    adapt.set_cache({"gpt-6-luna": ["/responses"], "gpt-5": ["/chat/completions"]})
    assert asyncio.run(adapt.upstream_kind("gpt-6-luna")) == "responses"
    assert asyncio.run(adapt.upstream_kind("gpt-5")) == "chat"
    assert asyncio.run(adapt.upstream_kind("unknown")) == "chat"
    assert asyncio.run(adapt.upstream_kind(None)) == "chat"


def test_model_endpoints_cache_only():
    adapt.set_cache({"mai-code-2-flash": ["/chat/completions", "/responses"]})
    assert adapt.model_endpoints("mai-code-2-flash") == frozenset({"/chat/completions", "/responses"})
    assert adapt.model_endpoints("unknown") is None
    assert adapt.model_endpoints(None) is None


def test_fallback_code_constant():
    assert adapt.FALLBACK_CODE == "no_available_model_endpoints"


def _messages_body() -> dict:
    return {
        "model": "gpt-6-luna", "max_tokens": 64, "stream": True,
        "system": [{"type": "text", "text": "be brief"}, {"type": "text", "text": "no fluff"}],
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": [
                {"type": "text", "text": "look"},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png", "data": "ABCD"}}]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "sure"},
                {"type": "tool_use", "id": "tu_1", "name": "f", "input": {"a": 1}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "result"}]},
        ],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "tool", "name": "f"},
        "temperature": 0.5, "top_p": 0.9,
        "stop_sequences": ["END"], "top_k": 3, "metadata": {"x": 1},
    }


def test_messages_to_responses_body_translation():
    out = adapt.messages_to_responses_body(_messages_body())
    assert out["model"] == "gpt-6-luna" and out["stream"] is True
    assert out["instructions"] == "be brief\n\nno fluff"
    assert out["input"][0] == {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    assert out["input"][1]["content"] == [{"type": "input_text", "text": "look"},
        {"type": "input_image", "image_url": "data:image/png;base64,ABCD"}]
    assert out["input"][2] == {"role": "assistant",
                               "content": [{"type": "output_text", "text": "sure"}]}
    assert out["input"][3] == {"type": "function_call", "call_id": "tu_1",
                               "name": "f", "arguments": '{"a": 1}'}
    assert out["input"][4] == {"type": "function_call_output", "call_id": "tu_1", "output": "result"}
    assert out["max_output_tokens"] == 64
    assert out["tools"] == [{"type": "function", "name": "f", "description": "d",
                             "parameters": {"type": "object"}}]
    assert out["tool_choice"] == {"type": "function", "name": "f"}
    assert out["temperature"] == 0.5 and out["top_p"] == 0.9
    for dropped in ("stop_sequences", "top_k", "metadata"):
        assert dropped not in out


def test_messages_tool_choice_variants():
    assert adapt.messages_to_responses_body({"messages": [], "tool_choice": "any"})["tool_choice"] == "required"
    assert adapt.messages_to_responses_body({"messages": [], "tool_choice": "auto"})["tool_choice"] == "auto"
    out = adapt.messages_to_responses_body({"messages": [], "system": "sys", "tool_choice": "none"})
    assert out["tool_choice"] == "none" and out["instructions"] == "sys"


def test_responses_to_messages_response_shape():
    resp = {"id": "r1", "status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": "alpha"},
                                         {"type": "output_text", "text": "beta"}]},
        {"type": "function_call", "call_id": "c9", "name": "f", "arguments": '{"a": 1}'}],
        "usage": {"input_tokens": 4, "output_tokens": 2},
        "copilot_usage": {"total_nano_aiu": 555}}
    out = adapt.responses_to_messages_response(resp, "gpt-6-luna")
    assert out["type"] == "message" and out["role"] == "assistant" and out["model"] == "gpt-6-luna"
    assert out["content"][0] == {"type": "text", "text": "alpha\nbeta"}
    assert out["content"][1] == {"type": "tool_use", "id": "c9", "name": "f", "input": {"a": 1}}
    assert out["stop_reason"] == "tool_use" and out["stop_sequence"] is None
    assert out["usage"] == {"input_tokens": 4, "output_tokens": 2}
    assert out["copilot_usage"] == {"total_nano_aiu": 555}


def test_messages_stop_reason_max_tokens_and_end_turn():
    out = adapt.responses_to_messages_response({"status": "incomplete", "output": [], "usage": {}}, "m")
    assert out["stop_reason"] == "max_tokens" and out["content"] == []
    out = adapt.responses_to_messages_response({"status": "completed", "output": [], "usage": {}}, "m")
    assert out["stop_reason"] == "end_turn"


def test_sse_to_chat_translation(monkeypatch):
    recorded = []
    monkeypatch.setattr(usage, "record", lambda *a: recorded.append(a))

    async def source():
        yield b'data: {"type":"response.output_item.added","item":{"type":"message","role":"assistant"}}\n\n'
        yield (b'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
               b'data: {"type":"response.output_text.delta","delta":" world"}\n\n')
        yield b'data: {"type":"response.reasoning_summary_text.delta","delta":"thinking"}\n\n'
        yield b'data: {"type":"response.function_call_arguments_delta","call_id":"call_1",' \
              b'"name":"get_weather","delta":"{\\"city"}\n\n'
        yield b'data: {"type":"response.function_call_arguments_delta","call_id":"call_1",' \
              b'"delta":":\\"SF\\"}"}\n\n'
        yield b"data: not json\n\n"
        yield (b'data: {"type":"response.completed","response":{"id":"resp_1",'
               b'"usage":{"input_tokens":7,"output_tokens":3},'
               b'"copilot_usage":{"total_nano_aiu":1234567890}}}\n\n')
        yield b"data: [DONE]\n\n"
        yield b'data: {"type":"response.output_text.delta","delta":"AFTER"}\n\n'

    async def drain(gen):
        return [c async for c in gen]

    chunks = asyncio.run(drain(sse_to_chat(source(), key_name="k", model="m")))
    objs = [json.loads(c[6:].strip()) for c in chunks if c.startswith(b"data: ")
            and c.strip() != b"data: [DONE]"]
    assert chunks[-1] == b"data: [DONE]\n\n"
    assert objs[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert objs[1]["choices"][0]["delta"] == {"content": "Hello"}
    assert objs[2]["choices"][0]["delta"] == {"content": " world"}
    tc1 = objs[3]["choices"][0]["delta"]["tool_calls"][0]
    assert tc1["id"] == "call_1" and tc1["function"]["name"] == "get_weather"
    assert tc1["function"]["arguments"] == '{"city'
    tc2 = objs[4]["choices"][0]["delta"]["tool_calls"][0]
    assert "id" not in tc2 and "name" not in tc2["function"]
    assert tc2["function"]["arguments"] == ':"SF"}'
    final = objs[5]
    assert final["choices"][0]["delta"] == {}
    assert final["choices"][0]["finish_reason"] == "tool_calls"
    assert final["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert final["copilot_usage"] == {"total_nano_aiu": 1234567890}
    assert len(objs) == 6  # reasoning frames and post-[DONE] frames are ignored
    assert recorded == [("k", "m", 7, 3, 1234567890)]
