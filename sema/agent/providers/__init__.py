"""Provider registry — same order and ids as the VS Code extension."""

from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import (
    BaseProvider,
    Event,
    ModelInfo,
    Notice,
    Provider,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    TurnEnd,
    TurnRequest,
    Usage,
)
from .cli_agent import (
    ClaudeCodeProvider,
    CliProvider,
    CodexProvider,
    CursorProvider,
    GrokProvider,
    OpenCodeProvider,
)
from .openai_compat import (
    OpenAICompatibleProvider,
    deepseek_provider,
    openai_provider,
    openrouter_provider,
    together_provider,
)

__all__ = [
    "AnthropicProvider", "BaseProvider", "CliProvider", "ClaudeCodeProvider",
    "CodexProvider", "CursorProvider", "Event", "GrokProvider", "ModelInfo",
    "Notice", "OpenAICompatibleProvider", "OpenCodeProvider", "Provider",
    "TextDelta", "ThinkingDelta", "ToolCall", "TurnEnd", "TurnRequest", "Usage",
    "PROVIDERS", "DEFAULT_PROVIDER", "get_provider", "provider_ids",
]


def _build() -> list[BaseProvider]:
    # Local CLI providers first — they reuse an existing login, so no key needed.
    return [
        ClaudeCodeProvider(),
        CodexProvider(),
        OpenCodeProvider(),
        GrokProvider(),
        CursorProvider(),
        AnthropicProvider(),
        openai_provider(),
        deepseek_provider(),
        openrouter_provider(),
        together_provider(),
    ]


PROVIDERS: list[BaseProvider] = _build()


# Claude Code reuses an existing local login, so it works with no key set up —
# which makes it the right default for a first run.
DEFAULT_PROVIDER = "claude-code"


def get_provider(provider_id: str | None) -> BaseProvider:
    """Look up a provider, falling back to the default."""
    for provider in PROVIDERS:
        if provider.id == provider_id:
            return provider
    return next(p for p in PROVIDERS if p.id == DEFAULT_PROVIDER)


def provider_ids() -> list[str]:
    return [p.id for p in PROVIDERS]
