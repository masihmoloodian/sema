"""
API providers driven against mock servers that speak the real wire protocol.

These exercise the actual Anthropic and OpenAI SDK client code plus this
project's stream parsing — the layer unit tests cannot reach, because it only
runs when bytes come back over HTTP. No API key required: the client is pointed
at a local server.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sema.agent.providers.anthropic import AnthropicProvider
from sema.agent.providers.base import Notice, TextDelta, ThinkingDelta, TurnEnd, Usage
from sema.agent.providers.openai_compat import OpenAICompatibleProvider, ModelInfo
from sema.agent.session import ChatMessage
from sema.agent.tools import Tool


class _Handler(BaseHTTPRequestHandler):
    """Replays a canned SSE body and records the request it received."""

    sse_body = b""
    recorded: list = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        type(self).recorded.append(json.loads(raw))
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self.wfile.write(type(self).sse_body)
        self.wfile.flush()

    def log_message(self, *_args):
        return


def serve(body: bytes):
    """Start a one-off SSE server; returns (base_url, handler_class, shutdown)."""
    handler = type("H", (_Handler,), {"sse_body": body, "recorded": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return f"http://127.0.0.1:{port}", handler, server.shutdown


def drain(provider, request):
    async def go():
        return [event async for event in provider.stream(request)]

    return asyncio.run(go())


def _sse(events: list[tuple[str, dict]]) -> bytes:
    out = b""
    for name, data in events:
        out += f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()
    return out


# ── Anthropic ───────────────────────────────────────────────────────────────


ANTHROPIC_STREAM = _sse([
    ("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-4-8",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 0,
                  "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0,
                             "content_block": {"type": "thinking", "thinking": "",
                                               "signature": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "thinking_delta", "thinking": "weighing it"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("content_block_start", {"type": "content_block_start", "index": 1,
                             "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1,
                             "delta": {"type": "text_delta", "text": "Hello "}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1,
                             "delta": {"type": "text_delta", "text": "world"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    ("message_delta", {"type": "message_delta",
                       "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                       "usage": {"output_tokens": 25}}),
    ("message_stop", {"type": "message_stop"}),
])

ANTHROPIC_TOOL_STREAM = _sse([
    ("message_start", {"type": "message_start", "message": {
        "id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-4-8",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 0}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0,
                             "content_block": {"type": "tool_use", "id": "toolu_1",
                                               "name": "read_file", "input": {}}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "input_json_delta",
                                       "partial_json": '{"path": "app.py"}'}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta",
                       "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                       "usage": {"output_tokens": 12}}),
    ("message_stop", {"type": "message_stop"}),
])


def _anthropic_request(base_url, **kwargs):
    from sema.agent.providers.base import TurnRequest

    request = TurnRequest(
        model="claude-opus-4-8",
        system=kwargs.pop("system", "SYSTEM PROMPT"),
        messages=kwargs.pop("messages", [ChatMessage("user", "hi")]),
        tools=kwargs.pop("tools", []),
        api_key="test-key",
        **kwargs,
    )
    return request


@pytest.fixture
def anthropic_provider(monkeypatch):
    def make(body):
        base_url, handler, shutdown = serve(body)
        provider = AnthropicProvider()
        original = provider._client

        def patched(api_key):
            client = original(api_key or "test-key")
            client.base_url = base_url
            return client

        monkeypatch.setattr(provider, "_client", patched)
        return provider, handler, shutdown

    return make


def test_anthropic_streams_text_and_thinking(anthropic_provider):
    provider, _handler, shutdown = anthropic_provider(ANTHROPIC_STREAM)
    try:
        events = drain(provider, _anthropic_request(None))
    finally:
        shutdown()
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hello world"
    assert "".join(e.text for e in events if isinstance(e, ThinkingDelta)) == "weighing it"
    end = [e for e in events if isinstance(e, TurnEnd)][0]
    assert end.stop_reason == "end_turn"
    assert end.tool_calls == []


def test_anthropic_reports_usage_and_cost(anthropic_provider):
    provider, _handler, shutdown = anthropic_provider(ANTHROPIC_STREAM)
    try:
        events = drain(provider, _anthropic_request(None))
    finally:
        shutdown()
    usage = [e for e in events if isinstance(e, Usage)][0]
    assert usage.output_tokens == 25
    assert usage.cached_input_tokens == 40
    assert usage.cost_usd is not None and usage.cost_usd > 0


def test_anthropic_request_body_matches_the_api_contract(anthropic_provider):
    """The wire body is where API drift actually bites."""
    tool = Tool("read_file", "Read a file",
                {"type": "object", "properties": {"path": {"type": "string"}}}, lambda: "")
    provider, handler, shutdown = anthropic_provider(ANTHROPIC_STREAM)
    try:
        drain(provider, _anthropic_request(None, tools=[tool], effort="xhigh"))
    finally:
        shutdown()
    body = handler.recorded[0]
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "xhigh"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0]["name"] == "read_file"
    assert "input_schema" in body["tools"][0]
    for banned in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert banned not in json.dumps(body)


def test_anthropic_parses_a_tool_call(anthropic_provider):
    tool = Tool("read_file", "Read a file",
                {"type": "object", "properties": {"path": {"type": "string"}}}, lambda: "")
    provider, _handler, shutdown = anthropic_provider(ANTHROPIC_TOOL_STREAM)
    request = _anthropic_request(None, tools=[tool])
    try:
        events = drain(provider, request)
    finally:
        shutdown()
    end = [e for e in events if isinstance(e, TurnEnd)][0]
    assert len(end.tool_calls) == 1
    assert end.tool_calls[0].name == "read_file"
    assert end.tool_calls[0].arguments == {"path": "app.py"}
    # The assistant turn is preserved so the follow-up call continues it.
    assert request.scratch and request.scratch[0]["role"] == "assistant"


def test_anthropic_surfaces_a_transport_error(anthropic_provider):
    provider, _handler, shutdown = anthropic_provider(b"")  # empty body -> parse error
    try:
        events = drain(provider, _anthropic_request(None))
    finally:
        shutdown()
    assert any(isinstance(e, Notice) for e in events)
    end = [e for e in events if isinstance(e, TurnEnd)][0]
    assert end.stop_reason == "error"


# ── OpenAI-compatible ───────────────────────────────────────────────────────


def _openai_sse(chunks: list[dict]) -> bytes:
    out = b""
    for chunk in chunks:
        out += f"data: {json.dumps(chunk)}\n\n".encode()
    out += b"data: [DONE]\n\n"
    return out


OPENAI_STREAM = _openai_sse([
    {"id": "c1", "object": "chat.completion.chunk", "model": "m",
     "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hi "}}]},
    {"id": "c1", "object": "chat.completion.chunk", "model": "m",
     "choices": [{"index": 0, "delta": {"content": "there"}}]},
    {"id": "c1", "object": "chat.completion.chunk", "model": "m",
     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
     "usage": {"prompt_tokens": 80, "completion_tokens": 12,
               "prompt_tokens_details": {"cached_tokens": 30}}},
])

OPENAI_TOOL_STREAM = _openai_sse([
    {"id": "c2", "object": "chat.completion.chunk", "model": "m",
     "choices": [{"index": 0, "delta": {"tool_calls": [
         {"index": 0, "id": "call_1", "type": "function",
          "function": {"name": "read_file", "arguments": '{"pa'}}]}}]},
    {"id": "c2", "object": "chat.completion.chunk", "model": "m",
     "choices": [{"index": 0, "delta": {"tool_calls": [
         {"index": 0, "function": {"arguments": 'th": "app.py"}'}}]}}]},
    {"id": "c2", "object": "chat.completion.chunk", "model": "m",
     "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
     "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
])


def _openai_provider(base_url, provider_id="openai"):
    return OpenAICompatibleProvider(
        id=provider_id,
        label=provider_id,
        key_env="TEST_KEY",
        base_url=base_url + "/v1",
        default_model="test-model",
        efforts=("default", "low", "high"),
        models=[ModelInfo("test-model", "Test")],
        prices={"test-model": (1.0, 0.1, 2.0)},
    )


def _openai_request(**kwargs):
    from sema.agent.providers.base import TurnRequest

    return TurnRequest(
        model="test-model",
        system=kwargs.pop("system", "SYSTEM"),
        messages=kwargs.pop("messages", [ChatMessage("user", "hi")]),
        tools=kwargs.pop("tools", []),
        api_key="test-key",
        **kwargs,
    )


def test_openai_streams_text_and_usage():
    base_url, _handler, shutdown = serve(OPENAI_STREAM)
    try:
        events = drain(_openai_provider(base_url), _openai_request())
    finally:
        shutdown()
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hi there"
    usage = [e for e in events if isinstance(e, Usage)][0]
    assert usage.input_tokens == 80
    assert usage.cached_input_tokens == 30
    assert usage.cost_usd == pytest.approx(80e-6 - 30e-6 + 30 * 0.1e-6 + 12 * 2e-6)


def test_openai_reassembles_a_split_tool_call():
    """Arguments arrive as indexed fragments and must be concatenated."""
    base_url, _handler, shutdown = serve(OPENAI_TOOL_STREAM)
    tool = Tool("read_file", "d", {"type": "object", "properties": {}}, lambda: "")
    request = _openai_request(tools=[tool])
    try:
        events = drain(_openai_provider(base_url), request)
    finally:
        shutdown()
    end = [e for e in events if isinstance(e, TurnEnd)][0]
    assert len(end.tool_calls) == 1
    assert end.tool_calls[0].name == "read_file"
    assert end.tool_calls[0].arguments == {"path": "app.py"}
    assert request.scratch[0]["tool_calls"][0]["id"] == "call_1"


def test_openai_request_body_shape():
    base_url, handler, shutdown = serve(OPENAI_STREAM)
    tool = Tool("read_file", "d", {"type": "object", "properties": {}}, lambda: "")
    try:
        drain(_openai_provider(base_url), _openai_request(tools=[tool], effort="high"))
    finally:
        shutdown()
    body = handler.recorded[0]
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["tools"][0]["type"] == "function"
    assert body["reasoning_effort"] == "high"


def test_openai_surfaces_a_transport_error():
    base_url, _handler, shutdown = serve(b"not-sse")
    try:
        events = drain(_openai_provider(base_url), _openai_request())
    finally:
        shutdown()
    end = [e for e in events if isinstance(e, TurnEnd)][0]
    assert end.stop_reason == "error"
    assert any(isinstance(e, Notice) for e in events)


@pytest.mark.parametrize("provider_id", ["openai", "deepseek", "openrouter", "together"])
def test_every_openai_family_provider_streams(provider_id):
    """All four share one implementation; prove each is wired end to end."""
    base_url, _handler, shutdown = serve(OPENAI_STREAM)
    try:
        events = drain(_openai_provider(base_url, provider_id), _openai_request())
    finally:
        shutdown()
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hi there"


# ── misconfiguration must degrade, never crash ──────────────────────────────


def test_openai_missing_key_is_a_notice_not_a_traceback(monkeypatch):
    """A missing key must not raise — it would kill the TUI mid-turn."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        id="openai", label="OpenAI", key_env="OPENAI_API_KEY",
        default_model="m", models=[ModelInfo("m")],
    )
    request = _openai_request()
    request.api_key = None
    events = drain(provider, request)
    notices = [e.text for e in events if isinstance(e, Notice)]
    assert notices and "OPENAI_API_KEY" in notices[0]
    assert [e for e in events if isinstance(e, TurnEnd)][0].stop_reason == "error"


def test_anthropic_missing_key_is_a_notice_not_a_traceback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    provider = AnthropicProvider()

    def explode(_api_key):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(provider, "_client", explode)
    request = _anthropic_request(None)
    request.api_key = None
    events = drain(provider, request)
    notices = [e.text for e in events if isinstance(e, Notice)]
    assert notices and "ANTHROPIC_API_KEY" in notices[0]
    assert [e for e in events if isinstance(e, TurnEnd)][0].stop_reason == "error"


@pytest.mark.parametrize("message,expected", [
    ("Could not resolve authentication method. Expected api_key", "not authenticated"),
    ("Error code: 401 - unauthorized", "not authenticated"),
    ("rate_limit_error: too many requests", "rate limit"),
    ("Error code: 404 - model not found", "rejected the model id"),
    ("connection reset by peer", "request failed"),
])
def test_anthropic_errors_are_explained_actionably(message, expected):
    from sema.agent.providers.anthropic import _explain

    assert expected in _explain(RuntimeError(message))


def test_anthropic_auth_error_names_the_fix():
    from sema.agent.providers.anthropic import _explain

    text = _explain(RuntimeError("authentication failed"))
    assert "ANTHROPIC_API_KEY" in text
    assert "ant auth login" in text
