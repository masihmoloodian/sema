"""
Anthropic provider — the Messages API with adaptive thinking and tool use.

One ``stream()`` call is one Messages request. Tool calls come back on
``TurnEnd`` and the loop feeds results in through ``request.scratch``, which
holds this provider's native assistant/tool-result message pairs.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

from ..attachments import materialize, read_base64
from ..session import Attachment
from .base import (
    BaseProvider,
    Event,
    ModelInfo,
    Notice,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    TurnEnd,
    TurnRequest,
    Usage,
)

# $ per 1M tokens (input, cached input, output), for the cost estimate.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-4-8": (5.0, 0.5, 25.0),
    "claude-opus-4-7": (5.0, 0.5, 25.0),
    "claude-sonnet-5": (3.0, 0.3, 15.0),
    "claude-haiku-4-5": (1.0, 0.1, 5.0),
    "claude-fable-5": (10.0, 1.0, 50.0),
}

# Models on the adaptive-thinking surface: `budget_tokens`, temperature, top_p
# and top_k are rejected there, and `display` must be set to see reasoning.
_ADAPTIVE = {
    "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5", "claude-fable-5",
}

_EFFORT_MODELS = _ADAPTIVE


def estimate_cost(model: str, usage: Usage) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    fresh_in, cached_in, out = price
    cached = usage.cached_input_tokens
    uncached = max(usage.input_tokens - cached, 0)
    return (
        uncached * fresh_in / 1_000_000
        + cached * cached_in / 1_000_000
        + usage.output_tokens * out / 1_000_000
    )


class AnthropicProvider(BaseProvider):
    id = "anthropic"
    label = "Claude (Anthropic)"
    requires_key = True
    reads_workspace = False
    default_model = "claude-opus-4-8"
    key_env = "ANTHROPIC_API_KEY"
    efforts = ("default", "low", "medium", "high", "xhigh", "max")
    models = [
        ModelInfo("claude-opus-4-8", "Claude Opus 4.8", "Most capable Opus tier",
                  recommended=True, accepts=("image", "pdf", "text")),
        ModelInfo("claude-sonnet-5", "Claude Sonnet 5", "Best speed/intelligence balance",
                  accepts=("image", "pdf", "text")),
        ModelInfo("claude-haiku-4-5", "Claude Haiku 4.5", "Fastest and cheapest",
                  accepts=("image", "pdf", "text"), efforts=()),
        ModelInfo("claude-opus-4-7", "Claude Opus 4.7", "Previous-generation Opus",
                  accepts=("image", "pdf", "text")),
        ModelInfo("claude-fable-5", "Claude Fable 5", "Most capable; premium pricing",
                  accepts=("image", "pdf", "text")),
    ]

    def _client(self, api_key: str | None):
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "The `anthropic` package is required for this provider. "
                "Install it with: uv sync --extra chat"
            ) from exc
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        # A bare client also resolves an `ant auth login` profile, so an unset
        # key is not necessarily an error.
        return AsyncAnthropic(api_key=key) if key else AsyncAnthropic()

    def _tool_specs(self, request: TurnRequest) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in request.tools
        ]

    def _content_blocks(
        self, text: str, attachments: list[Attachment], directory: str | None
    ) -> Any:
        if not attachments or not directory:
            return text
        from pathlib import Path

        blocks: list[dict[str, Any]] = []
        for attachment in attachments:
            try:
                data = read_base64(Path(directory), attachment)
            except OSError:
                continue
            if attachment.kind == "image":
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": attachment.mime, "data": data},
                })
            elif attachment.kind == "pdf":
                blocks.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                })
        blocks.append({"type": "text", "text": text})
        return blocks

    def _build_messages(self, request: TurnRequest) -> list[dict[str, Any]]:
        from pathlib import Path

        directory = request.attachments_dir
        history = request.messages
        if directory:
            history, _ = materialize(Path(directory), history)
        messages: list[dict[str, Any]] = []
        for message in history:
            messages.append({
                "role": message.role,
                "content": self._content_blocks(
                    message.content, message.attachments, directory
                ),
            })
        messages.extend(request.scratch)
        return messages

    async def stream(self, request: TurnRequest) -> AsyncIterator[Event]:
        try:
            client = self._client(request.api_key)
        except Exception as exc:  # noqa: BLE001 - missing key must not crash the UI
            yield Notice(
                f"Anthropic is not configured: {exc}. Set ANTHROPIC_API_KEY, run "
                "`ant auth login`, or switch provider with /provider."
            )
            yield TurnEnd(stop_reason="error")
            return
        model = request.model or self.default_model
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": self._build_messages(request),
        }
        if request.system:
            # Cache the system block: it is large (repo map + workflow) and
            # stable across turns, so this is the single biggest cost lever.
            params["system"] = [{
                "type": "text",
                "text": request.system,
                "cache_control": {"type": "ephemeral"},
            }]
        if request.tools:
            params["tools"] = self._tool_specs(request)
        if model in _ADAPTIVE:
            # Adaptive is the only supported thinking mode on these models;
            # `display` must be explicit or the reasoning stream arrives empty.
            params["thinking"] = {"type": "adaptive", "display": "summarized"}
        if request.effort and request.effort != "default" and model in _EFFORT_MODELS:
            params["output_config"] = {"effort": request.effort}

        tool_calls: list[ToolCall] = []
        assistant_content: list[dict[str, Any]] = []
        usage = Usage()
        try:
            async with client.messages.stream(**params) as stream:
                async for event in stream:
                    kind = getattr(event, "type", "")
                    if kind == "content_block_delta":
                        delta = event.delta
                        delta_type = getattr(delta, "type", "")
                        if delta_type == "text_delta":
                            yield TextDelta(delta.text)
                        elif delta_type == "thinking_delta":
                            yield ThinkingDelta(delta.thinking)
                final = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            yield Notice(_explain(exc))
            yield TurnEnd(stop_reason="error")
            return

        for block in final.content:
            block_type = getattr(block, "type", "")
            if block_type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
            assistant_content.append(
                block.model_dump() if hasattr(block, "model_dump") else block
            )

        raw = final.usage
        usage = Usage(
            input_tokens=getattr(raw, "input_tokens", 0) or 0,
            output_tokens=getattr(raw, "output_tokens", 0) or 0,
            cached_input_tokens=(getattr(raw, "cache_read_input_tokens", 0) or 0),
        )
        usage.input_tokens += getattr(raw, "cache_read_input_tokens", 0) or 0
        usage.input_tokens += getattr(raw, "cache_creation_input_tokens", 0) or 0
        usage.cost_usd = estimate_cost(model, usage)
        yield usage

        if tool_calls:
            # Preserve the assistant turn verbatim — thinking blocks included —
            # so the next request in this turn continues the same reasoning.
            request.scratch.append({"role": "assistant", "content": assistant_content})
        if final.stop_reason == "refusal":
            yield Notice("The model declined this request (safety refusal).")
        yield TurnEnd(tool_calls=tool_calls, stop_reason=final.stop_reason or "end_turn")

    def add_tool_results(
        self, request: TurnRequest, results: list[tuple[ToolCall, str, bool]]
    ) -> None:
        """Append tool results as one user message, as the API requires."""
        blocks = [
            {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": output or "(no output)",
                **({"is_error": True} if is_error else {}),
            }
            for call, output, is_error in results
        ]
        request.scratch.append({"role": "user", "content": blocks})

    def assistant_text_only(self, request: TurnRequest, text: str) -> None:
        request.scratch.append({"role": "assistant", "content": text})


_AUTH_HINT = (
    "Set ANTHROPIC_API_KEY, run `ant auth login`, or switch provider with "
    "/provider (the local CLI providers reuse an existing login)."
)


def _explain(exc: Exception) -> str:
    """Turn an SDK exception into something the user can act on."""
    text = str(exc)
    lowered = text.lower()
    if "authentication" in lowered or "api_key" in lowered or "401" in lowered:
        return f"Anthropic is not authenticated. {_AUTH_HINT}"
    if "rate_limit" in lowered or "429" in lowered:
        return "Anthropic rate limit reached — wait a moment and retry."
    if "not_found" in lowered or "404" in lowered:
        return f"Anthropic rejected the model id: {text}"
    return f"Anthropic request failed: {text}"
