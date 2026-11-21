"""Upstream header contract and vision detection (no token exchange)."""
import json

from crispy_copilot_gateway import copilot


def test_upstream_headers_shape():
    h = copilot.upstream_headers("gh")
    assert h["Authorization"] == "Bearer gh"
    assert h["X-GitHub-Api-Version"] == copilot.API_VERSION
    assert h["Openai-Intent"] == "conversation-edits"
    assert h["x-initiator"] == "agent"
    assert h["X-Interaction-Type"] == "conversation-agent"
    assert h["X-Interaction-Id"]
    assert h["User-Agent"].startswith("opencode/")
    assert "Copilot-Vision-Request" not in h


def test_upstream_headers_vision_and_interaction_id():
    assert copilot.upstream_headers("gh", vision=True)["Copilot-Vision-Request"] == "true"
    assert copilot.upstream_headers("gh", interaction_id="abc")["X-Interaction-Id"] == "abc"


def test_has_vision_openai_image_url():
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "x"}}]}]}).encode()
    assert copilot.has_vision(body) is True


def test_has_vision_responses_input_image():
    body = json.dumps({"input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "x"}]}]}).encode()
    assert copilot.has_vision(body) is True


def test_has_vision_plain_text_and_bad_input_false():
    assert copilot.has_vision(json.dumps({"messages": [{"content": "hi"}]}).encode()) is False
    assert copilot.has_vision(b"") is False
    assert copilot.has_vision(b"not json") is False
