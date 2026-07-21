"""
The agent loop.

One ``run_turn`` is a full user turn: call the provider, execute any tool calls
it asks for, feed results back, repeat until the model stops calling tools. The
loop is hand-written rather than delegating to an SDK tool runner because it has
to drive Anthropic, OpenAI-family, and CLI-backed providers through one path.

Everything the UI needs is yielded as an event, so the TUI, the headless
``--print`` mode, and the tests all consume the same stream.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from .permissions import PermissionManager
from .plan_artifact import read_plan, save_plan
from .providers import (
    BaseProvider,
    CliProvider,
    Notice,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    TurnEnd,
    TurnRequest,
    Usage,
)
from .session import ChatMessage, Session
from .tools import Tool, ToolContext, build_tools, execute
from .workflow import build_system

# Hard stop so a confused model cannot loop forever burning tokens.
MAX_ITERATIONS = 50


@dataclass
class ToolStarted:
    call_id: str
    name: str
    summary: str


@dataclass
class ToolFinished:
    call_id: str
    name: str
    output: str
    is_error: bool


@dataclass
class TurnComplete:
    text: str
    usage: Usage
    plan_path: str | None = None
    stop_reason: str = "end_turn"


LoopEvent = (
    TextDelta | ThinkingDelta | Notice | ToolStarted | ToolFinished | Usage | TurnComplete
)


@dataclass
class AgentConfig:
    root: Path
    provider: BaseProvider
    model: str
    mode: str = "agent"
    effort: str = "default"
    max_tokens: int = 16000
    api_key: str | None = None
    cli_bin: str | None = None
    use_index: bool = True
    project: str | None = None
    permissions: PermissionManager = field(default_factory=PermissionManager)


class Agent:
    """Drives one session against one provider."""

    def __init__(self, config: AgentConfig, session: Session,
                 attachments_dir: Path | None = None) -> None:
        self.config = config
        self.session = session
        self.attachments_dir = attachments_dir
        self.tool_context = ToolContext(
            root=config.root,
            permissions=config.permissions,
            project=config.project,
        )

    def tools(self) -> list[Tool]:
        if self.config.provider.reads_workspace:
            # CLI agents bring their own tools; giving them ours would double up.
            return []
        return build_tools(self.tool_context, self.config.mode, self.config.use_index)

    def _system(self) -> str:
        active_plan = ""
        if self.session.plan_path:
            active_plan = read_plan(self.config.root, self.session.plan_path)
        return build_system(
            context="",
            reads_workspace=self.config.provider.reads_workspace,
            mode=self.config.mode,
            active_plan=active_plan,
            active_plan_path=self.session.plan_path or "",
            use_index=self.config.use_index,
        )

    async def run_turn(self, user_text: str,
                       attachments: list[Any] | None = None) -> AsyncIterator[LoopEvent]:
        """Run one user turn to completion, yielding events as they happen."""
        provider = self.config.provider
        self.session.messages.append(
            ChatMessage(role="user", content=user_text, attachments=list(attachments or []))
        )

        tools = self.tools()
        request = TurnRequest(
            model=self.config.model,
            system=self._system(),
            messages=list(self.session.messages),
            tools=tools,
            max_tokens=self.config.max_tokens,
            effort=self.config.effort,
            api_key=self.config.api_key,
            cwd=str(self.config.root),
            cli_bin=self.config.cli_bin,
            session_id=self._resumable_cli_session(),
            mode=self.config.mode,
            attachments_dir=str(self.attachments_dir) if self.attachments_dir else None,
            permission_mode="bypass" if self.config.permissions.bypass else "ask",
        )
        if (
            provider.reads_workspace
            and self.config.mode == "agent"
            and not self.config.permissions.bypass
            and self.config.permissions.asker is None
        ):
            # These CLIs run non-interactively, so their own consent prompts
            # cannot be answered and every edit would come back denied. Only
            # warn when there is no interactive surface: a UI resolves this by
            # asking once, and repeating the warning every turn is just noise.
            yield Notice(
                f"{provider.label} runs its own tools and cannot ask for consent "
                "non-interactively. Start with --yes (or /mode plan) for edits to apply."
            )

        by_name = {tool.name: tool for tool in tools}
        collected: list[str] = []
        total = Usage()
        stop_reason = "end_turn"

        for _iteration in range(MAX_ITERATIONS):
            calls: list[ToolCall] = []
            turn_text: list[str] = []
            async for event in provider.stream(request):
                if isinstance(event, TextDelta):
                    turn_text.append(event.text)
                    collected.append(event.text)
                    yield event
                elif isinstance(event, (ThinkingDelta, Notice)):
                    yield event
                elif isinstance(event, Usage):
                    total.input_tokens += event.input_tokens
                    total.output_tokens += event.output_tokens
                    total.cached_input_tokens += event.cached_input_tokens
                    if event.cost_usd is not None:
                        total.cost_usd = (total.cost_usd or 0.0) + event.cost_usd
                    yield event
                elif isinstance(event, TurnEnd):
                    calls = event.tool_calls
                    stop_reason = event.stop_reason
            if not calls:
                break

            results: list[tuple[ToolCall, str, bool]] = []
            for call in calls:
                tool = by_name.get(call.name)
                if tool is None:
                    message = (
                        f"Unknown tool: {call.name}. Available: "
                        f"{', '.join(sorted(by_name)) or 'none'}"
                    )
                    yield ToolFinished(call.id, call.name, message, True)
                    results.append((call, message, True))
                    continue
                yield ToolStarted(call.id, call.name, tool.summary(call.arguments))
                output, is_error = await execute(tool, call.arguments, self.tool_context)
                yield ToolFinished(call.id, call.name, output, is_error)
                results.append((call, output, is_error))
            provider.add_tool_results(request, results)
        else:
            yield Notice(f"Stopped after {MAX_ITERATIONS} tool iterations.")

        answer = "".join(collected).strip()
        self.session.messages.append(ChatMessage(role="assistant", content=answer))
        self.session.usage.add(
            input_tokens=total.input_tokens,
            output_tokens=total.output_tokens,
            cached=total.cached_input_tokens,
            cost=total.cost_usd,
        )
        self._capture_cli_session()

        plan_path = None
        if self.config.mode == "plan" and answer:
            # Plan mode's single permitted side effect.
            artifact = save_plan(
                self.config.root, self.session.id, self.session.title, answer
            )
            plan_path = artifact.relative_path
            self.session.plan_path = plan_path

        yield TurnComplete(
            text=answer, usage=total, plan_path=plan_path, stop_reason=stop_reason
        )

    # ── CLI thread continuity ────────────────────────────────────────────

    def _resumable_cli_session(self) -> str | None:
        """Resume a CLI thread only when its execution contract still matches.

        Model and mode are part of that contract — resuming a plan-mode thread
        in agent mode would silently keep the old permissions.
        """
        session = self.session
        if not session.cli_session_id:
            return None
        if session.cli_session_provider != self.config.provider.id:
            return None
        if session.cli_session_model != self.config.model:
            return None
        if session.cli_session_mode != self.config.mode:
            return None
        return session.cli_session_id

    def _capture_cli_session(self) -> None:
        provider = self.config.provider
        if not isinstance(provider, CliProvider):
            return
        if provider.last_session_id:
            self.session.cli_session_id = provider.last_session_id
            self.session.cli_session_provider = provider.id
            self.session.cli_session_model = self.config.model
            self.session.cli_session_mode = self.config.mode
            self.session.cli_session_permission = (
                "bypass" if self.config.permissions.bypass else "ask"
            )


async def collect(events: AsyncIterator[LoopEvent]) -> TurnComplete:
    """Drain a turn and return its completion — the headless entry point."""
    final = TurnComplete(text="", usage=Usage())
    async for event in events:
        if isinstance(event, TurnComplete):
            final = event
    return final


def run_sync(agent: Agent, text: str) -> TurnComplete:
    """Blocking convenience wrapper for scripts and tests."""
    return asyncio.run(collect(agent.run_turn(text)))
