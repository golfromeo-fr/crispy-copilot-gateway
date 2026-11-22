"""Usage metering math and SSE/JSON parsing."""
from crispy_copilot_gateway import usage


def test_record_and_totals_math():
    usage.record("zcode", "gpt-5", 10, 5, 4473525000)
    t = usage.totals()
    assert t["requests"] == 1
    assert t["input_tokens"] == 10 and t["output_tokens"] == 5
    assert abs(t["credits"] - 4.473525) < 1e-9
    assert abs(t["cost_usd"] - 0.04473525) < 1e-9


def test_recent_newest_first():
    usage.record("a", "m1", 1, 1, 0)
    usage.record("b", "m2", 2, 2, 0)
    rows = usage.recent(10)
    assert rows[0]["key_name"] == "b" and rows[1]["key_name"] == "a"


def test_parse_sse_extracts_tokens_and_billing():
    buf = (b'data: {"x":1}\n\n'
           b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5},'
           b'"copilot_usage":{"total_nano_aiu":4473525000}}\n\n'
           b'data: [DONE]\n\n')
    assert usage.parse_sse(buf) == {"input_tokens": 10, "output_tokens": 5, "nano_aiu": 4473525000}


def test_parse_usage_openai_and_anthropic_shapes():
    assert usage.parse_usage({"usage": {"prompt_tokens": 3, "completion_tokens": 4}}) == {
        "input_tokens": 3, "output_tokens": 4}
    assert usage.parse_usage({"usage": {"input_tokens": 7, "output_tokens": 8}}) == {
        "input_tokens": 7, "output_tokens": 8}
    assert usage.parse_usage({}) == {}
    assert usage.parse_usage({"copilot_usage": {"total_nano_aiu": 1000000000}}) == {
        "nano_aiu": 1000000000}


def test_parse_sse_responses_nested_envelope():
    """Live shape: usage nested under response, copilot_usage on the event envelope."""
    buf = (b'data: {"type":"response.created","response":{"usage":{"input_tokens":7}}}\n\n'
           b'data: {"type":"response.completed","response":{"usage":{"input_tokens":7,"output_tokens":3}},'
           b'"copilot_usage":{"total_nano_aiu":4473525000}}\n\n')
    assert usage.parse_sse(buf) == {"input_tokens": 7, "output_tokens": 3, "nano_aiu": 4473525000}


def test_parse_usage_unwraps_response_envelope():
    # copilot_usage inside response (fallback) ...
    assert usage.parse_usage({"type": "response.completed", "response": {
        "usage": {"input_tokens": 2, "output_tokens": 1},
        "copilot_usage": {"total_nano_aiu": 5}}}) == {
            "input_tokens": 2, "output_tokens": 1, "nano_aiu": 5}
    # ... and on the envelope (the live native-/responses shape).
    assert usage.parse_usage({"type": "response.completed",
        "response": {"usage": {"input_tokens": 2, "output_tokens": 1}},
        "copilot_usage": {"total_nano_aiu": 5}}) == {
            "input_tokens": 2, "output_tokens": 1, "nano_aiu": 5}
