"""Protocol adaptation: chat<->Responses bodies/frames, no network (set_cache)."""
import asyncio
import json

import pytest

from crispy_copilot_gateway import adapt, adapt_stream, usage
from crispy_copilot_gateway.adapt_stream import sse_to_chat, sse_to_messages


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


@pytest.mark.parametrize("value", [True, False])
def test_chat_parallel_tool_calls_passthrough(value):
    out = adapt.chat_to_responses_body({"messages": [], "parallel_tool_calls": value})
    assert out["parallel_tool_calls"] is value


def test_chat_parallel_tool_calls_not_invented():
    assert "parallel_tool_calls" not in adapt.chat_to_responses_body({"messages": []})


@pytest.mark.parametrize("tool, expected", [
    ({"type": "function", "function": {"name": "f", "description": "d", "parameters": {"a": 1}}},
     {"type": "function", "name": "f", "description": "d", "parameters": {"a": 1}}),
    ({"type": "function", "function": {"name": "g"}}, {"type": "function", "name": "g"}),
], ids=["full", "name-only"])
def test_chat_tools_schema_regression(tool, expected):
    assert adapt.chat_to_responses_body({"messages": [], "tools": [tool]})["tools"] == [expected]


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


@pytest.mark.parametrize("choice, expected", [
    ({"type": "auto", "disable_parallel_tool_use": True}, "auto"),
    ({"type": "any", "disable_parallel_tool_use": True}, "required"),
    ({"type": "tool", "name": "f", "disable_parallel_tool_use": True},
     {"type": "function", "name": "f"}),
], ids=["auto", "any", "tool"])
def test_messages_disable_parallel_maps_to_parallel_false(choice, expected):
    out = adapt.messages_to_responses_body({"messages": [], "tool_choice": choice})
    assert out["tool_choice"] == expected
    assert out["parallel_tool_calls"] is False


@pytest.mark.parametrize("extra", [
    {},
    {"tool_choice": {"type": "auto"}},
    {"tool_choice": {"type": "auto", "disable_parallel_tool_use": False}},
    {"tool_choice": {"type": "any"}},
], ids=["absent", "auto", "auto-flag-false", "any"])
def test_messages_parallel_not_forced_off(extra):
    assert "parallel_tool_calls" not in adapt.messages_to_responses_body({"messages": [], **extra})


@pytest.mark.parametrize("tool, expected", [
    ({"name": "f", "description": "d", "input_schema": {"type": "object"}},
     {"type": "function", "name": "f", "description": "d", "parameters": {"type": "object"}}),
    ({"name": "g"}, {"type": "function", "name": "g"}),
], ids=["full", "name-only"])
def test_messages_tools_schema_regression(tool, expected):
    assert adapt.messages_to_responses_body({"messages": [], "tools": [tool]})["tools"] == [expected]


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
        yield b'data: {"type":"response.function_call_arguments.delta","call_id":"call_1",' \
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


def test_sse_to_chat_multi_call_indexes(monkeypatch):
    """Copilot's live shape: dot-form deltas keyed by item_id; N calls -> N indexes."""
    recorded = []
    monkeypatch.setattr(usage, "record", lambda *a: recorded.append(a))

    async def source():
        yield b'data: {"type":"response.output_item.added","item":{"id":"it1","type":"function_call","call_id":"call_A","name":"get_weather"}}\n\n'
        yield b'data: {"type":"response.output_item.added","item":{"id":"it2","type":"function_call","call_id":"call_B","name":"get_time"}}\n\n'
        yield b'data: {"type":"response.function_call_arguments.delta","item_id":"it1","delta":"{\\"city\\": \\"Tokyo\\"}"}\n\n'
        yield b'data: {"type":"response.function_call_arguments.done","item_id":"it1","arguments":"{\\"city\\": \\"Tokyo\\"}"}\n\n'
        yield b'data: {"type":"response.function_call_arguments.delta","item_id":"it2","delta":"{\\"city\\": \\"Paris\\"}"}\n\n'
        yield b'data: {"type":"response.function_call_arguments.done","item_id":"it2","arguments":"{\\"city\\": \\"Paris\\"}"}\n\n'
        yield b'data: {"type":"response.completed","response":{"id":"resp_2","usage":{"input_tokens":10,"output_tokens":5}}}\n\n'

    async def drain(gen):
        return [c async for c in gen]

    chunks = asyncio.run(drain(sse_to_chat(source(), key_name="k", model="m")))
    objs = [json.loads(c[6:].strip()) for c in chunks if c.startswith(b"data: ")
            and c.strip() != b"data: [DONE]"]
    calls = [o["choices"][0]["delta"]["tool_calls"][0] for o in objs
             if o["choices"][0]["delta"].get("tool_calls")]
    assert [c["index"] for c in calls] == [0, 1]
    assert [c["id"] for c in calls] == ["call_A", "call_B"]
    assert [c["function"]["name"] for c in calls] == ["get_weather", "get_time"]
    assert [c["function"]["arguments"] for c in calls] == ['{"city": "Tokyo"}', '{"city": "Paris"}']
    assert chunks[-1] == b"data: [DONE]\n\n"
    assert recorded == [("k", "m", 10, 5, None)]


def test_sse_to_chat_args_only_in_done(monkeypatch):
    """Upstream variant that skips delta events: full args arrive in arguments.done."""
    monkeypatch.setattr(usage, "record", lambda *a: None)

    async def source():
        yield b'data: {"type":"response.output_item.added","item":{"id":"it1","type":"function_call","call_id":"call_A","name":"f"}}\n\n'
        yield b'data: {"type":"response.function_call_arguments.done","item_id":"it1","arguments":"{\\"a\\": 1}"}\n\n'
        yield b'data: {"type":"response.completed","response":{"id":"resp_3","usage":{"input_tokens":1,"output_tokens":1}}}\n\n'

    async def drain(gen):
        return [c async for c in gen]

    chunks = asyncio.run(drain(sse_to_chat(source())))
    objs = [json.loads(c[6:].strip()) for c in chunks if c.startswith(b"data: ")
            and c.strip() != b"data: [DONE]"]
    calls = [o["choices"][0]["delta"]["tool_calls"][0] for o in objs
             if o["choices"][0]["delta"].get("tool_calls")]
    assert calls == [{"id": "call_A", "index": 0, "type": "function",
                      "function": {"name": "f", "arguments": '{"a": 1}'}}]


def test_sse_to_messages_dot_form_tool_args(monkeypatch):
    """Anthropic re-encoding: dot-form deltas stream input_json_delta; done adds remainder."""
    recorded = []
    monkeypatch.setattr(usage, "record", lambda *a: recorded.append(a))

    async def source():
        yield b'data: {"type":"response.created","response":{"id":"resp_9","model":"gpt-6-luna","usage":{"input_tokens":4}}}\n\n'
        yield b'data: {"type":"response.output_item.added","item":{"id":"it1","type":"function_call","call_id":"call_A","name":"get_weather"}}\n\n'
        yield b'data: {"type":"response.function_call_arguments.delta","item_id":"it1","delta":"{\\"city\\": \\"Tok"}\n\n'
        yield b'data: {"type":"response.function_call_arguments.delta","item_id":"it1","delta":"yo\\"}"}\n\n'
        yield b'data: {"type":"response.function_call_arguments.done","item_id":"it1","arguments":"{\\"city\\": \\"Tokyo\\"}"}\n\n'
        yield b'data: {"type":"response.output_item.done","item":{"id":"it1","type":"function_call","call_id":"call_A","name":"get_weather","arguments":"{\\"city\\": \\"Tokyo\\"}"}}\n\n'
        yield b'data: {"type":"response.completed","response":{"id":"resp_9","usage":{"input_tokens":4,"output_tokens":9}}}\n\n'

    async def drain(gen):
        return [c async for c in gen]

    chunks = asyncio.run(drain(sse_to_messages(source(), key_name="k", model="m")))
    events = [json.loads(c.split(b"data: ", 1)[1]) for c in chunks if b"data: " in c]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "message_start"
    assert "content_block_start" in kinds
    partials = [e["delta"]["partial_json"] for e in events
                if e["type"] == "content_block_delta"
                and e["delta"]["type"] == "input_json_delta"]
    assert "".join(partials) == '{"city": "Tokyo"}'
    assert kinds[-1] == "message_stop"
    assert kinds.count("content_block_stop") == 1
    stops = [e for e in events if e["type"] == "message_delta"]
    assert stops[0]["delta"]["stop_reason"] == "tool_use"
    assert recorded == [("k", "m", 4, 9, None)]


def test_sse_to_messages_args_only_in_output_item_done(monkeypatch):
    """Fallback: no added event, no deltas - block opens at done and full args stream."""
    monkeypatch.setattr(usage, "record", lambda *a: None)

    async def source():
        yield b'data: {"type":"response.created","response":{"id":"resp_x","usage":{"input_tokens":1}}}\n\n'
        yield b'data: {"type":"response.output_item.done","item":{"id":"it1","type":"function_call","call_id":"call_A","name":"f","arguments":"{\\"a\\": 1}"}}\n\n'
        yield b'data: {"type":"response.completed","response":{"id":"resp_x","usage":{"input_tokens":1,"output_tokens":1}}}\n\n'

    async def drain(gen):
        return [c async for c in gen]

    chunks = asyncio.run(drain(sse_to_messages(source())))
    events = [json.loads(c.split(b"data: ", 1)[1]) for c in chunks if b"data: " in c]
    starts = [e["content_block"] for e in events if e["type"] == "content_block_start"]
    assert starts and starts[-1]["type"] == "tool_use" and starts[-1]["id"] == "call_A"
    partials = [e["delta"]["partial_json"] for e in events
                if e["type"] == "content_block_delta"
                and e["delta"]["type"] == "input_json_delta"]
    assert "".join(partials) == '{"a": 1}'


def test_sse_to_messages_args_done_without_deltas(monkeypatch):
    """Regression: arguments.done with no prior deltas must not KeyError on streamed[idx]."""
    monkeypatch.setattr(usage, "record", lambda *a: None)

    async def source():
        yield b'data: {"type":"response.created","response":{"id":"resp_k","usage":{"input_tokens":1}}}\n\n'
        yield b'data: {"type":"response.output_item.added","item":{"id":"it1","type":"function_call","call_id":"call_A","name":"f"}}\n\n'
        yield b'data: {"type":"response.function_call_arguments.done","item_id":"it1","arguments":"{\\"a\\": 1}"}\n\n'
        yield b'data: {"type":"response.completed","response":{"id":"resp_k","usage":{"input_tokens":1,"output_tokens":1}}}\n\n'

    async def drain(gen):
        return [c async for c in gen]

    chunks = asyncio.run(drain(sse_to_messages(source())))
    events = [json.loads(c.split(b"data: ", 1)[1]) for c in chunks if b"data: " in c]
    partials = [e["delta"]["partial_json"] for e in events
                if e["type"] == "content_block_delta"
                and e["delta"]["type"] == "input_json_delta"]
    assert "".join(partials) == '{"a": 1}'


def test_messages_tool_choice_dict_forms():
    """Anthropic clients send {"type": "auto"|"any"|"none"|"tool"} - all must map."""
    assert adapt.messages_to_responses_body(
        {"messages": [], "tool_choice": {"type": "auto"}})["tool_choice"] == "auto"
    assert adapt.messages_to_responses_body(
        {"messages": [], "tool_choice": {"type": "any"}})["tool_choice"] == "required"
    assert adapt.messages_to_responses_body(
        {"messages": [], "tool_choice": {"type": "none"}})["tool_choice"] == "none"
    assert adapt.messages_to_responses_body(
        {"messages": [], "tool_choice": {"type": "tool", "name": "f"}})["tool_choice"] == {
            "type": "function", "name": "f"}
    assert "tool_choice" not in adapt.messages_to_responses_body(
        {"messages": [], "tool_choice": {"type": "bogus"}})


def test_messages_tool_result_error_and_image():
    body = {"model": "m", "messages": [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1", "name": "f", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "is_error": True,
            "content": [{"type": "text", "text": "boom"},
                        {"type": "image", "source": {"type": "base64",
                                                     "media_type": "image/png", "data": "ABCD"}}]}]},
    ]}
    out = adapt.messages_to_responses_body(body)
    fco = [i for i in out["input"] if i.get("type") == "function_call_output"][0]
    assert isinstance(fco["output"], list)
    assert fco["output"][0] == {"type": "input_text", "text": "[tool error]"}
    assert {"type": "input_text", "text": "boom"} in fco["output"]
    assert {"type": "input_image", "image_url": "data:image/png;base64,ABCD"} in fco["output"]


def test_messages_tool_result_text_only_error_prefix():
    body = {"model": "m", "messages": [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "is_error": True, "content": "failed"}]},
    ]}
    out = adapt.messages_to_responses_body(body)
    fco = [i for i in out["input"] if i.get("type") == "function_call_output"][0]
    assert fco["output"] == "[tool error]\nfailed"


def test_chat_tool_result_image_preserved():
    body = {"model": "m", "messages": [
        {"role": "tool", "tool_call_id": "c1", "content": [
            {"type": "text", "text": "see"},
            {"type": "image_url", "image_url": {"url": "http://x/i.png"}}]},
    ]}
    out = adapt.chat_to_responses_body(body)
    fco = [i for i in out["input"] if i.get("type") == "function_call_output"][0]
    assert fco["output"] == [{"type": "input_text", "text": "see"},
                             {"type": "input_image", "image_url": "http://x/i.png"}]


def test_sse_to_chat_incomplete_finish_length(monkeypatch):
    monkeypatch.setattr(usage, "record", lambda *a: None)

    async def source():
        yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
        yield b'data: {"type":"response.incomplete","response":{"id":"r","status":"incomplete","incomplete_details":{"reason":"max_output_tokens"},"usage":{"input_tokens":1,"output_tokens":1}}}\n\n'

    async def drain(g):
        return [c async for c in g]

    chunks = asyncio.run(drain(sse_to_chat(source())))
    objs = [json.loads(c[6:].strip()) for c in chunks if c.startswith(b"data: ")
            and c.strip() != b"data: [DONE]"]
    assert objs[-1]["choices"][0]["finish_reason"] == "length"


def test_sse_to_chat_tool_only_sends_role(monkeypatch):
    monkeypatch.setattr(usage, "record", lambda *a: None)

    async def source():
        yield b'data: {"type":"response.output_item.added","item":{"id":"it1","type":"function_call","call_id":"call_A","name":"f"}}\n\n'
        yield b'data: {"type":"response.function_call_arguments.delta","item_id":"it1","delta":"{}"}\n\n'
        yield b'data: {"type":"response.completed","response":{"id":"r","usage":{"input_tokens":1,"output_tokens":1}}}\n\n'

    async def drain(g):
        return [c async for c in g]

    chunks = asyncio.run(drain(sse_to_chat(source())))
    objs = [json.loads(c[6:].strip()) for c in chunks if c.startswith(b"data: ")
            and c.strip() != b"data: [DONE]"]
    assert objs[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert objs[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_sse_to_messages_tool_then_text_sequential_indices(monkeypatch):
    """Tool-first then text must yield unique, monotonically increasing block indices."""
    monkeypatch.setattr(usage, "record", lambda *a: None)

    async def source():
        yield b'data: {"type":"response.created","response":{"id":"r","usage":{"input_tokens":1}}}\n\n'
        yield b'data: {"type":"response.output_item.added","item":{"id":"it1","type":"function_call","call_id":"call_A","name":"f"}}\n\n'
        yield b'data: {"type":"response.function_call_arguments.delta","item_id":"it1","delta":"{}"}\n\n'
        yield b'data: {"type":"response.output_item.done","item":{"id":"it1","type":"function_call","call_id":"call_A","name":"f","arguments":"{}"}}\n\n'
        yield b'data: {"type":"response.output_text.delta","item_id":"it2","delta":"hello"}\n\n'
        yield b'data: {"type":"response.completed","response":{"id":"r","usage":{"input_tokens":1,"output_tokens":1}}}\n\n'

    async def drain(g):
        return [c async for c in g]

    chunks = asyncio.run(drain(sse_to_messages(source())))
    events = [json.loads(c.split(b"data: ", 1)[1]) for c in chunks if b"data: " in c]
    starts = [(e["index"], e["content_block"]["type"]) for e in events
              if e["type"] == "content_block_start"]
    assert starts == [(0, "tool_use"), (1, "text")]
    assert [e["index"] for e in events if e["type"] == "content_block_stop"] == [0, 1]


def test_sse_to_messages_multiple_text_segments_unique_indices(monkeypatch):
    """A second text segment after a tool gets a fresh index - never reuses a closed one."""
    monkeypatch.setattr(usage, "record", lambda *a: None)

    async def source():
        yield b'data: {"type":"response.created","response":{"id":"r","usage":{"input_tokens":1}}}\n\n'
        yield b'data: {"type":"response.output_item.added","item":{"id":"m1","type":"message"}}\n\n'
        yield b'data: {"type":"response.output_text.delta","item_id":"m1","delta":"a"}\n\n'
        yield b'data: {"type":"response.output_item.done","item":{"id":"m1","type":"message"}}\n\n'
        yield b'data: {"type":"response.output_item.added","item":{"id":"it1","type":"function_call","call_id":"call_A","name":"f"}}\n\n'
        yield b'data: {"type":"response.function_call_arguments.delta","item_id":"it1","delta":"{}"}\n\n'
        yield b'data: {"type":"response.output_item.added","item":{"id":"m2","type":"message"}}\n\n'
        yield b'data: {"type":"response.output_text.delta","item_id":"m2","delta":"b"}\n\n'
        yield b'data: {"type":"response.completed","response":{"id":"r","usage":{"input_tokens":1,"output_tokens":1}}}\n\n'

    async def drain(g):
        return [c async for c in g]

    chunks = asyncio.run(drain(sse_to_messages(source())))
    events = [json.loads(c.split(b"data: ", 1)[1]) for c in chunks if b"data: " in c]
    starts = [e["index"] for e in events if e["type"] == "content_block_start"]
    assert starts == [0, 1, 2]
    assert len(set(starts)) == len(starts)


def test_sse_to_chat_incomplete_meters_usage(monkeypatch):
    """Failed/incomplete terminal frames carrying usage must be metered (not just completed)."""
    recorded = []
    monkeypatch.setattr(usage, "record", lambda *a: recorded.append(a))

    async def source():
        yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
        yield b'data: {"type":"response.incomplete","response":{"id":"r","status":"incomplete","usage":{"input_tokens":5,"output_tokens":2}},"copilot_usage":{"total_nano_aiu":99}}\n\n'

    async def drain(g):
        return [c async for c in g]

    asyncio.run(drain(sse_to_chat(source(), key_name="k", model="m")))
    assert recorded == [("k", "m", 5, 2, 99)]


def test_sse_to_messages_top_level_usage_fallback(monkeypatch):
    """Usage on the event envelope (not nested under response) is still metered."""
    recorded = []
    monkeypatch.setattr(usage, "record", lambda *a: recorded.append(a))

    async def source():
        yield b'data: {"type":"response.created","response":{"id":"r"}}\n\n'
        yield b'data: {"type":"response.completed","response":{"id":"r"},"usage":{"input_tokens":4,"output_tokens":3},"copilot_usage":{"total_nano_aiu":7}}\n\n'

    async def drain(g):
        return [c async for c in g]

    asyncio.run(drain(sse_to_messages(source(), key_name="k", model="m")))
    assert recorded == [("k", "m", 4, 3, 7)]


def _stream_bytes(events: list[dict], separator: bytes = b"\r\n") -> bytes:
    return b"".join(b"data: " + json.dumps(event, ensure_ascii=False).encode() + separator + separator
                   for event in events)


def _source_chunks(chunks: list[bytes]):
    async def source():
        for chunk in chunks:
            yield chunk
    return source()


def _drain(gen):
    async def run():
        return [chunk async for chunk in gen]
    return asyncio.run(run())


def _fragmentation_fixture() -> bytes:
    return _stream_bytes([
        {"type": "response.created", "response": {"id": "frag"}},
        {"type": "response.output_item.added", "item": {"id": "msg1", "type": "message"}},
        {"type": "response.output_text.delta", "item_id": "msg1", "delta": "café 🙂"},
        {"type": "response.output_item.added", "item": {
            "id": "tool1", "type": "function_call", "call_id": "call1", "name": "f"}},
        {"type": "response.function_call_arguments.delta", "item_id": "tool1", "delta": "{\"x\":"},
        {"type": "response.function_call_arguments.delta", "item_id": "tool1", "delta": "1}"},
        {"type": "response.completed", "response": {"id": "frag", "usage": {
            "input_tokens": 1, "output_tokens": 2}}},
    ])


@pytest.mark.parametrize("translator", [sse_to_chat, sse_to_messages])
def test_sse_reencoders_handle_every_single_byte_split(monkeypatch, translator):
    """Every byte boundary (including UTF-8 and CRLF interiors) preserves output."""
    monkeypatch.setattr(adapt_stream.uuid, "uuid4", lambda: type("Id", (), {"hex": "fixed"})())
    stream = _fragmentation_fixture()
    expected = _drain(translator(_source_chunks([stream, b""])))

    for boundary in range(len(stream) + 1):
        actual = _drain(translator(_source_chunks([
            b"", stream[:boundary], b"", stream[boundary:], b"" ])))
        assert actual == expected, f"output changed when split at byte {boundary}"


def test_sse_reencoders_accept_final_data_line_without_newline(monkeypatch):
    """A complete final data line is processed even when the stream omits LF."""
    monkeypatch.setattr(adapt_stream.uuid, "uuid4", lambda: type("Id", (), {"hex": "fixed"})())
    line = b'data: {"type":"response.output_text.delta","delta":"tail"}'
    chat = _drain(sse_to_chat(_source_chunks([line])))
    messages = _drain(sse_to_messages(_source_chunks([line])))
    assert any(b'"content":"tail"' in chunk for chunk in chat)
    assert any(b'"text":"tail"' in chunk for chunk in messages)


def test_sse_reencoders_reassemble_parallel_tiny_tool_deltas(monkeypatch):
    monkeypatch.setattr(adapt_stream.uuid, "uuid4", lambda: type("Id", (), {"hex": "fixed"})())
    events = [
        {"type": "response.output_item.added", "item": {
            "id": f"item{i}", "type": "function_call", "call_id": f"call{i}", "name": f"tool{i}"}}
        for i in range(3)
    ]
    args = ["{\"alpha\":1}", "{\"beta\":2}", "{\"gamma\":3}"]
    for pos in range(max(map(len, args))):
        for i, value in enumerate(args):
            if pos < len(value):
                events.append({"type": "response.function_call_arguments.delta",
                               "item_id": f"item{i}", "delta": value[pos]})
    events.extend({"type": "response.function_call_arguments.done", "item_id": f"item{i}",
                   "arguments": args[i]} for i in range(3))
    events.append({"type": "response.completed", "response": {"usage": {}}})
    stream = _stream_bytes(events)

    chat = _drain(sse_to_chat(_source_chunks([stream])))
    chat_objects = [json.loads(chunk[6:]) for chunk in chat if chunk.startswith(b"data: ")
                    and chunk != b"data: [DONE]\n\n"]
    chat_calls = {}
    for obj in chat_objects:
        for call in obj["choices"][0]["delta"].get("tool_calls", []):
            chat_calls.setdefault(call["index"], {"id": call.get("id"), "args": ""})
            chat_calls[call["index"]]["args"] += call["function"].get("arguments", "")
    assert len(chat_calls) == 3
    assert len({call["id"] for call in chat_calls.values()}) == 3
    assert [chat_calls[i]["args"] for i in range(3)] == args

    messages = _drain(sse_to_messages(_source_chunks([stream])))
    message_events = [json.loads(chunk.split(b"data: ", 1)[1]) for chunk in messages if b"data: " in chunk]
    starts = [event for event in message_events if event["type"] == "content_block_start"]
    partials = {}
    for event in message_events:
        if event["type"] == "content_block_delta" and event["delta"]["type"] == "input_json_delta":
            partials.setdefault(event["index"], "")
            partials[event["index"]] += event["delta"]["partial_json"]
    assert [event["index"] for event in starts] == [0, 1, 2]
    assert len(set(event["index"] for event in starts)) == 3
    assert [partials[i] for i in range(3)] == args


@pytest.mark.parametrize("ordering", ["text-tools", "tools-text", "text-tool-text"])
def test_sse_to_messages_output_item_ordering_has_monotonic_indices(monkeypatch, ordering):
    monkeypatch.setattr(usage, "record", lambda *a: None)
    monkeypatch.setattr(adapt_stream.uuid, "uuid4", lambda: type("Id", (), {"hex": "fixed"})())
    text = lambda mid, value: [
        {"type": "response.output_item.added", "item": {"id": mid, "type": "message"}},
        {"type": "response.output_text.delta", "item_id": mid, "delta": value},
        {"type": "response.output_item.done", "item": {"id": mid, "type": "message"}},
    ]
    tool = lambda tid: [
        {"type": "response.output_item.added", "item": {
            "id": tid, "type": "function_call", "call_id": tid, "name": "f"}},
        {"type": "response.function_call_arguments.delta", "item_id": tid, "delta": "{}"},
        {"type": "response.output_item.done", "item": {
            "id": tid, "type": "function_call", "call_id": tid, "name": "f", "arguments": "{}"}},
    ]
    events = {"text-tools": text("m1", "a") + tool("t1"),
              "tools-text": tool("t1") + text("m1", "a"),
              "text-tool-text": text("m1", "a") + tool("t1") + text("m2", "b")}[ordering]
    events.append({"type": "response.completed", "response": {"usage": {}}})
    chunks = _drain(sse_to_messages(_source_chunks([_stream_bytes(events)])))
    output = [json.loads(chunk.split(b"data: ", 1)[1]) for chunk in chunks if b"data: " in chunk]
    indices = [event["index"] for event in output if event["type"] == "content_block_start"]
    assert indices == list(range(len(indices)))
    assert len(indices) == len(set(indices))


def test_sse_to_chat_output_item_done_without_added(monkeypatch):
    monkeypatch.setattr(usage, "record", lambda *a: None)
    events = [
        {"type": "response.output_item.done", "item": {
            "id": "it", "type": "function_call", "call_id": "call", "name": "f",
            "arguments": "{\"ok\":true}"}},
        {"type": "response.completed", "response": {"usage": {}}},
    ]
    chunks = _drain(sse_to_chat(_source_chunks([_stream_bytes(events)])))
    objs = [json.loads(chunk[6:]) for chunk in chunks if chunk.startswith(b"data: ")
            and chunk != b"data: [DONE]\n\n"]
    calls = [call for obj in objs for call in obj["choices"][0]["delta"].get("tool_calls", [])]
    assert calls and calls[0]["function"]["arguments"] == "{\"ok\":true}"
