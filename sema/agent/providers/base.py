"""
Provider abstraction.

Every provider normalizes to one event stream so a single agent loop drives all
of them. A provider performs exactly one model call per ``stream()`` invocation
and reports any tool calls it wants; the loop executes those and calls again.
That split is what keeps the loop provider-agnostic — the alternative (an
SDK-native tool runner) is Anthropic-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

from ..session import ChatMessage
from ..tools import Tool


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    description: str = ""
    recommended: bool = False
    section: str = ""
    accepts: tuple[str, ...] = ("text",)
    # None means "inherit the provider's levels"; an empty tuple means this
    # model has no effort control at all. The two are not the same.
    efforts: tuple[str, ...] | None = None

    @property
    def label(self) -> str:
        return self.name or self.id


# ── normalized stream events ────────────────────────────────────────────────


@dataclass
class TextDelta:
    text: str


@dataclass
class ThinkingDelta:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float | None = None


@dataclass
class Notice:
    """Provider-level information worth showing but not part of the answer."""

    text: str


@dataclass
class TurnEnd:
    """End of one model call. ``tool_calls`` non-empty means: run them, call again."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


Event = TextDelta | ThinkingDelta | ToolCall | Usage | Notice | TurnEnd


@dataclass
class TurnRequest:
    model: str
    system: str
    messages: list[ChatMessage]
    tools: list[Tool]
    max_tokens: int = 16000
    effort: str = "default"
    api_key: str | None = None
    cwd: str | None = None
    cli_bin: str | None = None
    session_id: str | None = None
    mode: str = "agent"
    attachments_dir: str | None = None
    # 'ask' or 'bypass'. CLI agents run non-interactively under `-p`, so their
    # own consent prompts cannot be answered — agent mode needs 'bypass'.
    permission_mode: str = "ask"
    # Assistant/tool history accumulated within this turn, in provider-native
    # form. The loop hands back whatever the provider gave it, so each provider
    # owns its own wire representation of intermediate tool exchanges.
    scratch: list[Any] = field(default_factory=list)


class Provider(Protocol):
    id: str
    label: str
    requires_key: bool
    reads_workspace: bool
    default_model: str
    models: list[ModelInfo]
    efforts: tuple[str, ...]

    def stream(self, request: TurnRequest) -> AsyncIterator[Event]:
        ...


class BaseProvider:
    """Shared defaults so concrete providers only declare what differs."""

    id = "base"
    label = "Base"
    requires_key = True
    reads_workspace = False
    default_model = ""
    models: list[ModelInfo] = []
    efforts: tuple[str, ...] = ()
    key_env: str = ""

    def model_info(self, model_id: str) -> ModelInfo | None:
        return next((m for m in self.models if m.id == model_id), None)

    def accepts(self, model_id: str) -> tuple[str, ...]:
        info = self.model_info(model_id)
        return info.accepts if info else ("text",)

    def efforts_for(self, model_id: str) -> tuple[str, ...]:
        info = self.model_info(model_id)
        if info is not None and info.efforts is not None:
            return info.efforts
        return self.efforts

    async def stream(self, request: TurnRequest) -> AsyncIterator[Event]:  # pragma: no cover
        raise NotImplementedError
        yield  # type: ignore[unreachable]
