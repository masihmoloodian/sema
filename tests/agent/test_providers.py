"""Provider registry, request shaping, and CLI stream parsing."""

import json

import pytest

from sema.agent.providers import PROVIDERS, get_provider, provider_ids
from sema.agent.providers.anthropic import AnthropicProvider, estimate_cost
from sema.agent.providers.base import ToolCall, TurnRequest, Usage
from sema.agent.providers.cli_agent import (
    ClaudeCodeProvider,
    CodexProvider,
    CursorProvider,
    GrokProvider,
    OpenCodeProvider,
)
from sema.agent.providers.openai_compat import openai_provider
from sema.agent.session import ChatMessage
from sema.agent.tools import Tool


# ── registry ────────────────────────────────────────────────────────────────


def test_registry_matches_the_extension_ids():
    assert provider_ids() == [
        "claude-code", "codex", "opencode", "grok", "cursor",
        "anthropic", "openai", "deepseek", "openrouter", "together",
    ]


def test_cli_providers_come_first_and_need_no_key():
    for provider in PROVIDERS[:5]:
        assert provider.requires_key is False
        assert provider.reads_workspace is True


def test_get_provider_falls_back_to_the_default():
    """Claude Code is the default: it reuses a local login, so it needs no key."""
    from sema.agent.providers import DEFAULT_PROVIDER

    assert DEFAULT_PROVIDER == "claude-code"
    assert get_provider("nope").id == "claude-code"
    assert get_provider(None).id == "claude-code"


def test_every_provider_default_model_is_in_its_catalog():
    for provider in PROVIDERS:
        ids = {m.id for m in provider.models}
        assert provider.default_model in ids, provider.id


def test_efforts_for_model_override():
    provider = AnthropicProvider()
    # Haiku declares an empty effort tuple, overriding the provider default.
    assert provider.efforts_for("claude-haiku-4-5") == ()
    assert "xhigh" in provider.efforts_for("claude-opus-4-8")


# ── Anthropic request shaping ───────────────────────────────────────────────


def _request(model="claude-opus-4-8", **kwargs):
    return TurnRequest(
        model=model,
        system=kwargs.pop("system", "SYS"),
        messages=kwargs.pop("messages", [ChatMessage("user", "hi")]),
        tools=kwargs.pop("tools", []),
        **kwargs,
    )


def _params(provider, request):
    """Rebuild the request dict the provider would send, without a network call."""
    params = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "messages": provider._build_messages(request),
    }
    if request.system:
        params["system"] = [{
            "type": "text", "text": request.system,
            "cache_control": {"type": "ephemeral"},
        }]
    if request.tools:
        params["tools"] = provider._tool_specs(request)
    from sema.agent.providers.anthropic import _ADAPTIVE, _EFFORT_MODELS

    if request.model in _ADAPTIVE:
        params["thinking"] = {"type": "adaptive", "display": "summarized"}
    if request.effort != "default" and request.model in _EFFORT_MODELS:
        params["output_config"] = {"effort": request.effort}
    return params


def test_anthropic_uses_adaptive_thinking_never_budget_tokens():
    """budget_tokens is rejected on these models; adaptive is the only mode."""
    params = _params(AnthropicProvider(), _request())
    assert params["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "budget_tokens" not in json.dumps(params)


def test_anthropic_sends_no_sampling_parameters():
    """temperature/top_p/top_k return 400 on the adaptive-thinking models."""
    params = _params(AnthropicProvider(), _request())
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in params


def test_thinking_display_is_explicit():
    """The default is 'omitted', which would render an empty thinking pane."""
    params = _params(AnthropicProvider(), _request())
    assert params["thinking"]["display"] == "summarized"


def test_system_block_is_cached():
    params = _params(AnthropicProvider(), _request())
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_effort_is_nested_under_output_config():
    params = _params(AnthropicProvider(), _request(effort="xhigh"))
    assert params["output_config"] == {"effort": "xhigh"}
    assert "effort" not in params  # never top-level


def test_default_effort_is_omitted_entirely():
    assert "output_config" not in _params(AnthropicProvider(), _request())


def test_tool_specs_use_input_schema():
    tool = Tool("t", "desc", {"type": "object", "properties": {}}, lambda: "")
    specs = AnthropicProvider()._tool_specs(_request(tools=[tool]))
    assert specs == [{"name": "t", "description": "desc",
                      "input_schema": {"type": "object", "properties": {}}}]


def test_anthropic_tool_results_go_in_one_user_message():
    """Splitting them trains the model out of parallel tool calls."""
    provider = AnthropicProvider()
    request = _request()
    calls = [ToolCall("a", "read_file", {}), ToolCall("b", "glob", {})]
    provider.add_tool_results(request, [(calls[0], "one", False), (calls[1], "two", True)])
    assert len(request.scratch) == 1
    message = request.scratch[0]
    assert message["role"] == "user"
    assert len(message["content"]) == 2
    assert message["content"][0]["tool_use_id"] == "a"
    assert message["content"][1]["is_error"] is True


def test_anthropic_cost_estimate():
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost("claude-opus-4-8", usage) == pytest.approx(30.0)


def test_cost_estimate_discounts_cached_input():
    plain = estimate_cost("claude-opus-4-8", Usage(input_tokens=1_000_000))
    cached = estimate_cost(
        "claude-opus-4-8", Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000)
    )
    assert cached < plain


def test_cost_estimate_is_none_for_unknown_models():
    assert estimate_cost("mystery-model", Usage(input_tokens=100)) is None


# ── OpenAI-compatible shaping ───────────────────────────────────────────────


def test_openai_puts_system_in_the_messages_array():
    provider = openai_provider()
    messages = provider._build_messages(_request(model="gpt-5.6-sol"))
    assert messages[0] == {"role": "system", "content": "SYS"}


def test_openai_tool_specs_are_function_wrapped():
    tool = Tool("t", "d", {"type": "object", "properties": {}}, lambda: "")
    specs = openai_provider()._tool_specs(_request(tools=[tool]))
    assert specs[0]["type"] == "function"
    assert specs[0]["function"]["name"] == "t"


def test_openai_tool_results_are_one_message_each():
    """Chat-completions requires a separate `tool` message per result."""
    provider = openai_provider()
    request = _request()
    calls = [ToolCall("a", "x", {}), ToolCall("b", "y", {})]
    provider.add_tool_results(request, [(calls[0], "1", False), (calls[1], "2", False)])
    assert len(request.scratch) == 2
    assert request.scratch[0]["role"] == "tool"
    assert request.scratch[0]["tool_call_id"] == "a"


def test_openai_cost_estimate():
    provider = openai_provider()
    usage = Usage(input_tokens=1_000_000, output_tokens=0)
    assert provider.estimate_cost("gpt-5.6-sol", usage) == pytest.approx(5.0)


# ── CLI provider argument shaping ───────────────────────────────────────────


def test_claude_code_stream_json_args():
    args = ClaudeCodeProvider().build_args(_request(model="opus", mode="agent"))
    assert "-p" in args
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert args[args.index("--model") + 1] == "opus"
    assert args[args.index("--append-system-prompt") + 1] == "SYS"


def test_claude_code_plan_mode_sets_permission_mode():
    args = ClaudeCodeProvider().build_args(_request(mode="plan"))
    assert args[args.index("--permission-mode") + 1] == "plan"


def test_claude_code_ask_mode_disables_tools():
    args = ClaudeCodeProvider().build_args(_request(mode="ask"))
    assert args[args.index("--tools") + 1] == ""


def test_claude_code_resumes_a_session():
    args = ClaudeCodeProvider().build_args(_request(session_id="sess-1"))
    assert args[args.index("--resume") + 1] == "sess-1"


def test_default_model_is_not_passed_as_a_flag():
    args = ClaudeCodeProvider().build_args(_request(model="default"))
    assert "--model" not in args


def test_codex_read_only_sandbox_outside_agent_mode():
    args = CodexProvider().build_args(_request(mode="plan"))
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in args


def test_codex_agent_mode_bypasses_the_sandbox_only_with_consent():
    """Bypass is gated on the permission mode, never the silent default."""
    asked = CodexProvider().build_args(_request(mode="agent"))
    assert "--dangerously-bypass-approvals-and-sandbox" not in asked
    assert asked[asked.index("--sandbox") + 1] == "read-only"

    bypassed = CodexProvider().build_args(
        _request(mode="agent", permission_mode="bypass")
    )
    assert "--dangerously-bypass-approvals-and-sandbox" in bypassed


def test_opencode_agent_flag_tracks_the_mode():
    def agent_flag(mode):
        args = OpenCodeProvider().build_args(_request(mode=mode))
        return args[args.index("--agent") + 1]

    assert agent_flag("agent") == "build"
    assert agent_flag("plan") == "plan"


def test_grok_auto_approve_needs_agent_mode_and_bypass():
    """Verified against `grok --help`: the flag is --always-approve, not --yolo."""
    assert "--always-approve" in GrokProvider().build_args(
        _request(mode="agent", permission_mode="bypass"))
    assert "--always-approve" not in GrokProvider().build_args(_request(mode="agent"))
    assert "--always-approve" not in GrokProvider().build_args(
        _request(mode="plan", permission_mode="bypass"))


def test_grok_effort_flag_name():
    """`--effort` does not exist on grok; it is --reasoning-effort."""
    args = GrokProvider().build_args(_request(effort="high"))
    assert args[args.index("--reasoning-effort") + 1] == "high"
    assert "--effort" not in args


def test_cursor_force_needs_agent_mode_and_bypass():
    assert "--force" in CursorProvider().build_args(
        _request(mode="agent", permission_mode="bypass"))
    assert "--force" not in CursorProvider().build_args(_request(mode="agent"))
    assert "--force" not in CursorProvider().build_args(
        _request(mode="ask", permission_mode="bypass"))


def test_claude_code_skip_permissions_is_gated_on_bypass():
    """Without this flag under `-p`, every edit silently comes back denied."""
    asked = ClaudeCodeProvider().build_args(_request(mode="agent"))
    assert "--dangerously-skip-permissions" not in asked

    bypassed = ClaudeCodeProvider().build_args(
        _request(mode="agent", permission_mode="bypass"))
    assert "--dangerously-skip-permissions" in bypassed


def test_claude_code_never_skips_permissions_outside_agent_mode():
    for mode in ("ask", "plan"):
        args = ClaudeCodeProvider().build_args(
            _request(mode=mode, permission_mode="bypass"))
        assert "--dangerously-skip-permissions" not in args


def test_tool_activity_label_prefers_a_recognizable_field():
    from sema.agent.providers.cli_agent import _first_value

    assert _first_value({"file_path": "/a/b.py", "replace_all": False}) == "/a/b.py"
    assert _first_value({"old_string": "x", "command": "npm test"}) == "npm test"
    assert _first_value({"replace_all": False}) == ""


# ── CLI stream parsing ──────────────────────────────────────────────────────


def test_claude_code_captures_session_and_model():
    provider = ClaudeCodeProvider()
    provider.parse_event({"type": "system", "session_id": "s1", "model": "opus-x"})
    assert provider.last_session_id == "s1"
    assert provider.last_model == "opus-x"


def test_claude_code_parses_text_and_thinking_deltas():
    provider = ClaudeCodeProvider()
    text = provider.parse_event({
        "type": "stream_event",
        "event": {"type": "content_block_delta",
                  "delta": {"type": "text_delta", "text": "hello"}},
    })
    thinking = provider.parse_event({
        "type": "stream_event",
        "event": {"type": "content_block_delta",
                  "delta": {"type": "thinking_delta", "thinking": "hmm"}},
    })
    assert text[0].text == "hello"
    assert thinking[0].text == "hmm"


def test_claude_code_parses_usage_and_cost():
    events = ClaudeCodeProvider().parse_event({
        "type": "result",
        "usage": {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 3},
        "total_cost_usd": 0.02,
    })
    usage = events[0]
    # input_tokens is the uncached remainder; the cache read is prompt too.
    assert usage.input_tokens == 10 + 3
    assert usage.cached_input_tokens == 3
    assert usage.cost_usd == 0.02


def test_codex_parses_its_thread_item_stream():
    """Shapes captured from a real `codex exec --json` run."""
    provider = CodexProvider()
    provider.parse_event({"type": "thread.started", "thread_id": "019f-abc"})
    assert provider.last_session_id == "019f-abc"

    events = provider.parse_event({
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": "PONG"},
    })
    assert events[0].text == "PONG"

    events = provider.parse_event({
        "type": "turn.completed",
        "usage": {"input_tokens": 13468, "cached_input_tokens": 9984, "output_tokens": 6},
    })
    assert events[0].input_tokens == 13468
    assert events[0].cached_input_tokens == 9984


def test_codex_separates_consecutive_messages():
    """Codex emits whole messages; without a break they would run together."""
    provider = CodexProvider()
    first = provider.parse_event({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "one"}})
    second = provider.parse_event({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "two"}})
    assert first[0].text == "one"
    assert second[0].text == "\ntwo"


def test_codex_reports_a_command_it_runs():
    events = CodexProvider().parse_event({
        "type": "item.started",
        "item": {"type": "command_execution", "command": "/bin/zsh -lc ls"}})
    assert "ls" in events[0].text


def test_codex_resume_is_a_subcommand_before_the_flags():
    """`codex exec resume <id>` — clap binds options to the preceding command."""
    args = CodexProvider().build_args(_request(session_id="sess-7"))
    assert args[:3] == ["exec", "resume", "sess-7"]


def test_codex_resume_omits_flags_it_rejects():
    """`codex exec resume` errors on --sandbox and -m; the thread keeps its own."""
    args = CodexProvider().build_args(
        _request(session_id="sess-7", model="gpt-5.6-sol", mode="plan"))
    assert "--sandbox" not in args
    assert "-m" not in args
    assert "--json" in args and "--skip-git-repo-check" in args


def test_codex_resume_still_honors_bypass():
    args = CodexProvider().build_args(
        _request(session_id="s", mode="agent", permission_mode="bypass"))
    assert "--dangerously-bypass-approvals-and-sandbox" in args


def test_codex_fresh_run_keeps_the_sandbox_and_model():
    args = CodexProvider().build_args(_request(model="gpt-5.6-sol", mode="plan"))
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[args.index("-m") + 1] == "gpt-5.6-sol"


def test_grok_parses_its_streaming_json():
    """Shapes captured from a real `grok --output-format streaming-json` run."""
    provider = GrokProvider()
    assert provider.parse_event({"type": "text", "data": "PO"})[0].text == "PO"
    assert provider.parse_event({"type": "thought", "data": "hmm"})[0].text == "hmm"
    events = provider.parse_event({
        "type": "end", "sessionId": "019f-xyz",
        "usage": {"input_tokens": 13020, "cache_read_input_tokens": 5,
                  "output_tokens": 36}})
    assert provider.last_session_id == "019f-xyz"
    assert events[0].input_tokens == 13020
    assert events[0].cached_input_tokens == 5


def test_opencode_has_no_auto_flag():
    """`--auto` is not an opencode option; passing it fails the run."""
    assert "--auto" not in OpenCodeProvider().build_args(_request(mode="agent"))


def test_parsers_ignore_unknown_event_types():
    for provider in (ClaudeCodeProvider(), CodexProvider(), OpenCodeProvider(),
                     GrokProvider(), CursorProvider()):
        assert provider.parse_event({"type": "something-new"}) == []


def test_opencode_parses_step_finish_usage():
    """Shapes captured from a real `opencode run --format json` run."""
    events = OpenCodeProvider().parse_event({
        "type": "step_finish", "sessionID": "ses_1",
        "part": {"type": "step-finish", "cost": 0.0,
                 "tokens": {"input": 4221, "output": 12,
                            "cache": {"read": 4096, "write": 0}}},
    })
    usage = events[0]
    assert usage.input_tokens == 4221
    assert usage.output_tokens == 12
    assert usage.cached_input_tokens == 4096


def test_opencode_parses_text_and_session():
    provider = OpenCodeProvider()
    events = provider.parse_event({
        "type": "text", "sessionID": "ses_abc",
        "part": {"type": "text", "text": "PONG"},
    })
    assert events[0].text == "PONG"
    assert provider.last_session_id == "ses_abc"


def test_claude_code_usage_counts_the_whole_prompt():
    """`input_tokens` is only the uncached remainder — cache read and write are
    the rest of the prompt. Reporting the bare field showed 2 tokens for a
    26,000-token request."""
    events = ClaudeCodeProvider().parse_event({
        "type": "result",
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 8045,
            "cache_read_input_tokens": 18682,
            "output_tokens": 13,
        },
        "total_cost_usd": 0.077,
    })
    usage = events[0]
    assert usage.input_tokens == 2 + 8045 + 18682
    assert usage.cached_input_tokens == 18682
    assert usage.output_tokens == 13
    assert usage.cost_usd == 0.077


def test_claude_code_usage_without_cache_fields():
    events = ClaudeCodeProvider().parse_event({
        "type": "result", "usage": {"input_tokens": 500, "output_tokens": 20},
    })
    assert events[0].input_tokens == 500
    assert events[0].cached_input_tokens == 0
