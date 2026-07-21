"""
CLI-backed providers — Claude Code, Codex, opencode, Grok, Cursor.

These reuse an existing local login instead of an API key, and they inspect the
repository themselves (``reads_workspace = True``), so sema does not inject RAG
context or run its own tool loop for them: it streams their JSON output and maps
it onto the same event types every other provider emits.

Argument shapes mirror ``vscode-extension/src/providers/cli.ts`` so a session's
``cliSessionId`` resumes identically from either surface.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any, AsyncIterator

from .base import (
    BaseProvider,
    Event,
    ModelInfo,
    Notice,
    TextDelta,
    ThinkingDelta,
    TurnEnd,
    TurnRequest,
    Usage,
)


class CliProvider(BaseProvider):
    """Base for local agent CLIs that stream newline-delimited JSON."""

    requires_key = False
    reads_workspace = True
    binary = ""
    permission_modes = ("ask", "bypass")

    def __init__(self) -> None:
        # Reported back to the caller so the session can resume this thread.
        self.last_session_id: str | None = None
        self.last_model: str | None = None
        # Whether any assistant text has been emitted this turn — used by
        # providers that send whole messages instead of token deltas.
        self._emitted = False

    def executable(self, request: TurnRequest) -> str | None:
        return request.cli_bin or shutil.which(self.binary)

    def build_args(self, request: TurnRequest) -> list[str]:  # pragma: no cover - abstract
        raise NotImplementedError

    def parse_event(self, payload: dict[str, Any]) -> list[Event]:  # pragma: no cover
        raise NotImplementedError

    def prompt_for(self, request: TurnRequest) -> str:
        """Last user turn; earlier turns come from the CLI's own resumed thread."""
        for message in reversed(request.messages):
            if message.role == "user":
                return message.content
        return ""

    async def stream(self, request: TurnRequest) -> AsyncIterator[Event]:
        exe = self.executable(request)
        if not exe:
            yield Notice(
                f"`{self.binary}` was not found on PATH. Install it, or set the path "
                f"with /provider config."
            )
            yield TurnEnd(stop_reason="error")
            return
        args = self.build_args(request)
        prompt = self.prompt_for(request)
        self._emitted = False
        try:
            process = await asyncio.create_subprocess_exec(
                exe, *args, prompt,
                cwd=request.cwd,
                # Codex reads extra instructions from stdin when it is a pipe,
                # and would block forever waiting on ours.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            yield Notice(f"Could not start {self.binary}: {exc}")
            yield TurnEnd(stop_reason="error")
            return

        assert process.stdout is not None
        async for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            for event in self.parse_event(payload):
                yield event
        await process.wait()
        if process.returncode not in (0, None):
            stderr = b""
            if process.stderr is not None:
                stderr = await process.stderr.read()
            detail = stderr.decode("utf-8", errors="replace").strip()
            if detail:
                yield Notice(f"{self.binary} exited {process.returncode}: {detail[:800]}")
        # CLI agents run their own tool loop, so one stream is the whole turn.
        yield TurnEnd(stop_reason="end_turn")

    # CLI providers do their own tool execution; these are no-ops so the loop
    # can treat every provider uniformly.
    def add_tool_results(self, request: TurnRequest, results: list[Any]) -> None:
        return None

    def assistant_text_only(self, request: TurnRequest, text: str) -> None:
        return None


class ClaudeCodeProvider(CliProvider):
    id = "claude-code"
    label = "Claude Code (local CLI)"
    binary = "claude"
    default_model = "default"
    efforts = ("default", "low", "medium", "high", "xhigh", "max")
    models = [
        ModelInfo("default", "Default", "Whatever the CLI is configured to use",
                  recommended=True),
        ModelInfo("opus", "Opus"),
        ModelInfo("sonnet", "Sonnet"),
        ModelInfo("haiku", "Haiku"),
    ]

    def build_args(self, request: TurnRequest) -> list[str]:
        args = ["-p", "--output-format", "stream-json",
                "--include-partial-messages", "--verbose"]
        if request.attachments_dir:
            args += ["--add-dir", request.attachments_dir]
        if request.session_id:
            args += ["--resume", request.session_id]
        if request.mode == "plan":
            args += ["--permission-mode", "plan"]
        elif request.mode == "ask":
            args += ["--tools", ""]
        elif request.permission_mode == "bypass":
            # Under `-p` the CLI cannot ask for consent interactively, so
            # without this every edit comes back denied. Gated on bypass so it
            # is never the silent default.
            args.append("--dangerously-skip-permissions")
        if request.model and request.model != "default":
            args += ["--model", request.model]
        if request.effort and request.effort != "default":
            args += ["--effort", request.effort]
        if request.system:
            args += ["--append-system-prompt", request.system]
        return args

    def parse_event(self, payload: dict[str, Any]) -> list[Event]:
        events: list[Event] = []
        kind = payload.get("type")
        if kind == "system":
            if payload.get("session_id"):
                self.last_session_id = payload["session_id"]
            if payload.get("model"):
                self.last_model = payload["model"]
        elif kind == "stream_event":
            event = payload.get("event") or {}
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    events.append(TextDelta(delta.get("text", "")))
                elif delta.get("type") == "thinking_delta":
                    events.append(ThinkingDelta(delta.get("thinking", "")))
        elif kind == "assistant":
            for block in (payload.get("message") or {}).get("content", []):
                if block.get("type") == "tool_use":
                    name = block.get("name", "tool")
                    detail = _first_value(block.get("input") or {})
                    events.append(Notice(f"↳ {name} {detail}"))
        elif kind == "result":
            raw = payload.get("usage") or {}
            # `input_tokens` is only the *uncached remainder*. The full prompt is
            # that plus what was read from and written to the cache — reporting
            # the bare field showed 2 tokens for a 26,000-token prompt.
            cache_read = raw.get("cache_read_input_tokens", 0) or 0
            cache_write = raw.get("cache_creation_input_tokens", 0) or 0
            events.append(Usage(
                input_tokens=(raw.get("input_tokens", 0) or 0) + cache_read + cache_write,
                output_tokens=raw.get("output_tokens", 0) or 0,
                cached_input_tokens=cache_read,
                cost_usd=payload.get("total_cost_usd"),
            ))
        return events


class CodexProvider(CliProvider):
    id = "codex"
    label = "Codex (local CLI)"
    binary = "codex"
    default_model = "default"
    efforts = ("default", "minimal", "low", "medium", "high", "xhigh")
    models = [
        ModelInfo("default", "Default", recommended=True),
        ModelInfo("gpt-5.6-sol", "GPT-5.6 Sol"),
        ModelInfo("gpt-5.6-terra", "GPT-5.6 Terra"),
    ]

    def build_args(self, request: TurnRequest) -> list[str]:
        bypass = request.mode == "agent" and request.permission_mode == "bypass"
        if request.session_id:
            # `resume` is a subcommand taking SESSION_ID as its first
            # positional, and it accepts a narrower flag set than `exec` —
            # `--sandbox` and `-m` are rejected outright. The resumed thread
            # keeps the model and sandbox policy it was created with.
            args = ["exec", "resume", request.session_id,
                    "--json", "--skip-git-repo-check"]
            if bypass:
                args.append("--dangerously-bypass-approvals-and-sandbox")
            return args

        args = ["exec", "--json", "--skip-git-repo-check"]
        if bypass:
            args.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            args += ["--sandbox", "read-only"]
        if request.model and request.model != "default":
            args += ["-m", request.model]
        return args

    def parse_event(self, payload: dict[str, Any]) -> list[Event]:
        """Parse Codex's `--json` thread/turn/item event stream.

        Codex emits whole items rather than token deltas, so a completed
        `agent_message` is surfaced as one TextDelta.
        """
        events: list[Event] = []
        kind = payload.get("type")
        if kind == "thread.started":
            self.last_session_id = payload.get("thread_id")
        elif kind in ("item.started", "item.completed"):
            item = payload.get("item") or {}
            item_type = item.get("type")
            if item_type == "agent_message" and kind == "item.completed":
                text = item.get("text") or ""
                if text:
                    # Codex has no streaming deltas; separate messages in one
                    # turn need a break or they run together.
                    events.append(TextDelta(text if not self._emitted else "\n" + text))
                    self._emitted = True
            elif item_type == "reasoning" and kind == "item.completed":
                summary = item.get("text") or ""
                if summary:
                    events.append(ThinkingDelta(summary))
            elif item_type == "command_execution" and kind == "item.started":
                events.append(Notice(f"↳ {str(item.get('command', ''))[:120]}"))
            elif item_type in ("file_change", "patch_apply") and kind == "item.started":
                events.append(Notice(f"↳ edit {str(item.get('path', ''))[:100]}"))
        elif kind == "turn.completed":
            usage = payload.get("usage") or {}
            events.append(Usage(
                input_tokens=usage.get("input_tokens", 0) or 0,
                output_tokens=usage.get("output_tokens", 0) or 0,
                cached_input_tokens=usage.get("cached_input_tokens", 0) or 0,
            ))
        elif kind == "turn.failed":
            error = (payload.get("error") or {}).get("message", "turn failed")
            events.append(Notice(f"Codex: {error}"))
        return events


class OpenCodeProvider(CliProvider):
    id = "opencode"
    label = "opencode (local CLI)"
    binary = "opencode"
    default_model = "default"
    models = [ModelInfo("default", "Default", recommended=True)]

    def build_args(self, request: TurnRequest) -> list[str]:
        args = ["run", "--format", "json",
                "--agent", "build" if request.mode == "agent" else "plan"]
        if request.cwd:
            args += ["--dir", request.cwd]
        if request.session_id:
            args += ["--session", request.session_id]
        if request.model and request.model != "default":
            args += ["--model", request.model]
        return args

    def parse_event(self, payload: dict[str, Any]) -> list[Event]:
        events: list[Event] = []
        if payload.get("sessionID"):
            self.last_session_id = payload["sessionID"]
        part = payload.get("part") or payload
        kind = part.get("type")
        if kind == "text" and part.get("text"):
            events.append(TextDelta(part["text"]))
        elif kind == "reasoning" and part.get("text"):
            events.append(ThinkingDelta(part["text"]))
        elif kind == "tool":
            events.append(Notice(f"↳ {part.get('tool', 'tool')}"))
        elif kind == "step-finish":
            # opencode reports per-step tokens with cache read/write split out.
            tokens = part.get("tokens") or {}
            cache = tokens.get("cache") or {}
            events.append(Usage(
                input_tokens=tokens.get("input", 0) or 0,
                output_tokens=tokens.get("output", 0) or 0,
                cached_input_tokens=cache.get("read", 0) or 0,
                cost_usd=part.get("cost"),
            ))
        return events


class GrokProvider(CliProvider):
    id = "grok"
    label = "Grok Build (local CLI)"
    binary = "grok"
    default_model = "default"
    efforts = ("default", "low", "medium", "high")
    models = [ModelInfo("default", "Default", recommended=True)]

    def build_args(self, request: TurnRequest) -> list[str]:
        args = ["--output-format", "streaming-json"]
        if request.cwd:
            args += ["--cwd", request.cwd]
        if request.session_id:
            args += ["-r", request.session_id]
        if request.model and request.model != "default":
            args += ["-m", request.model]
        if request.effort and request.effort != "default":
            args += ["--reasoning-effort", request.effort]
        if request.mode == "agent" and request.permission_mode == "bypass":
            args.append("--always-approve")
        elif request.mode == "ask":
            args += ["--tools", "read_file,grep,list_dir"]
        if request.system:
            args += ["--rules", request.system]
        args.append("-p")
        return args

    def parse_event(self, payload: dict[str, Any]) -> list[Event]:
        """Parse Grok's `streaming-json` events.

        Grok streams token deltas under a `data` key and closes the turn with a
        single `end` event carrying the session id and usage.
        """
        events: list[Event] = []
        kind = payload.get("type")
        if kind == "text" and payload.get("data"):
            events.append(TextDelta(payload["data"]))
        elif kind == "thought" and payload.get("data"):
            events.append(ThinkingDelta(payload["data"]))
        elif kind in ("tool_use", "tool"):
            name = payload.get("name") or payload.get("tool") or "tool"
            events.append(Notice(f"↳ {name}"))
        elif kind == "end":
            if payload.get("sessionId"):
                self.last_session_id = payload["sessionId"]
            usage = payload.get("usage") or {}
            events.append(Usage(
                input_tokens=usage.get("input_tokens", 0) or 0,
                output_tokens=usage.get("output_tokens", 0) or 0,
                cached_input_tokens=usage.get("cache_read_input_tokens", 0) or 0,
            ))
        elif kind == "error":
            events.append(Notice(f"Grok: {payload.get('message', 'error')}"))
        return events


class CursorProvider(CliProvider):
    id = "cursor"
    label = "Cursor Agent (local CLI)"
    binary = "cursor-agent"
    default_model = "default"
    models = [ModelInfo("default", "Default", recommended=True)]

    def build_args(self, request: TurnRequest) -> list[str]:
        args = ["-p", "--output-format", "stream-json"]
        if request.cwd:
            args += ["--workspace", request.cwd]
        if request.session_id:
            args += ["--resume", request.session_id]
        if request.model and request.model != "default":
            args += ["--model", request.model]
        if request.mode == "agent" and request.permission_mode == "bypass":
            args.append("--force")
        return args

    def parse_event(self, payload: dict[str, Any]) -> list[Event]:
        events: list[Event] = []
        if payload.get("session_id"):
            self.last_session_id = payload["session_id"]
        if payload.get("type") == "assistant":
            for block in (payload.get("message") or {}).get("content", []):
                if block.get("type") == "text":
                    events.append(TextDelta(block.get("text", "")))
        return events


def _first_value(data: dict[str, Any]) -> str:
    """A short label for a tool call — prefer the fields a human recognizes."""
    for key in ("file_path", "path", "command", "pattern", "query", "url"):
        if data.get(key):
            return str(data[key])[:80]
    for value in data.values():
        # Skip booleans and other flags; they read as noise ("Edit False").
        if isinstance(value, str) and value.strip():
            return value[:80]
    return ""
