"""
The agent's tool surface.

Two families:

* **sema tools** — thin wrappers over ``sema.mcp.tools``, so the terminal app and
  the MCP server expose byte-identical behavior. The MCP contract holds:
  ``search_code`` returns signatures only; ``get_code`` is the only tool that
  returns full source.
* **coding tools** — ``read_file`` / ``write_file`` / ``edit_file`` / ``bash`` /
  ``glob`` / ``grep``, which is what makes this a coding agent rather than a
  search UI.

Tools are provider-agnostic: each carries a JSON Schema, and the provider
adapters translate to the wire format Anthropic or OpenAI expects.

Two guardrails are structural, not advisory:

* **Path confinement** — every model-supplied path is resolved and must stay
  under the project root. Symlink escapes, ``..``, and absolute outside paths
  are rejected before any filesystem call.
* **Edit staleness** — ``edit_file`` refuses to write a file that changed since
  the model last read it. This is the reason ``edit`` is a dedicated tool rather
  than ``bash sed``: the harness can enforce an invariant the shell cannot.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .permissions import ApprovalRequest, PermissionManager

# Commands routed through the devops analyze-first gate instead of raw exec.
DEVOPS_BINARIES = {"kubectl", "terraform", "aws", "helm"}

_MAX_OUTPUT = 30_000
_BASH_TIMEOUT = 120


class ToolError(Exception):
    """Raised for a recoverable tool failure; surfaced to the model as text."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[..., str]
    # Short human summary for the approval modal and the transcript's tool card.
    summarize: Callable[[dict[str, Any]], str] | None = None

    def summary(self, args: dict[str, Any]) -> str:
        if self.summarize:
            return self.summarize(args)
        first = next(iter(args.values()), "")
        return str(first)[:80]


@dataclass
class ToolContext:
    """Everything the tools need that is not a model argument."""

    root: Path
    permissions: PermissionManager
    # file path -> sha256 of the content the model last read. Drives staleness.
    read_hashes: dict[str, str] = field(default_factory=dict)
    project: str | None = None

    def resolve(self, raw: str) -> Path:
        """Resolve a model-supplied path, confined to the project root.

        Resolution happens before any filesystem call, and the check is on the
        fully-resolved path, so ``..`` traversal and symlinks that point outside
        the root are both caught.
        """
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        root = self.root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ToolError(
                f"Path escapes the project root and was blocked: {raw}"
            )
        return resolved

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root.resolve()))
        except ValueError:
            return str(path)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"


# ── sema index tools ────────────────────────────────────────────────────────


def _sema_tools(ctx: ToolContext) -> list[Tool]:
    from ..mcp import tools as mcp_tools

    project = ctx.project

    def wrap(fn: Callable[..., str], **fixed: Any) -> Callable[..., str]:
        def runner(**kwargs: Any) -> str:
            return fn(**{**kwargs, **fixed})

        return runner

    return [
        Tool(
            name="search_code",
            description=(
                "Semantic search over the indexed codebase. Returns matching symbol "
                "signatures with file and line — never bodies. This must be the first "
                "navigation call for any codebase question."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language description of what to find.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many results to return (1-10).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            run=wrap(mcp_tools.search_code, project=project),
            summarize=lambda a: a.get("query", ""),
        ),
        Tool(
            name="get_code",
            description=(
                "Return the full source of one symbol found via search_code. The only "
                "tool that returns bodies."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol_name": {"type": "string", "description": "Symbol to fetch."}
                },
                "required": ["symbol_name"],
            },
            run=wrap(mcp_tools.get_code, project=project),
            summarize=lambda a: a.get("symbol_name", ""),
        ),
        Tool(
            name="check_reuse",
            description=(
                "Before writing a new helper, class, function, or utility, check whether "
                "the codebase already has one. Returns a reuse verdict."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What the new code would do.",
                    }
                },
                "required": ["description"],
            },
            run=wrap(mcp_tools.check_reuse, project=project),
            summarize=lambda a: a.get("description", ""),
        ),
        Tool(
            name="repo_map",
            description="High-level architecture map of the project. Use for orientation.",
            parameters={"type": "object", "properties": {}},
            run=wrap(mcp_tools.repo_map, project=project),
            summarize=lambda _a: "architecture",
        ),
        Tool(
            name="find_usages",
            description="Find call sites and references of a symbol across the codebase.",
            parameters={
                "type": "object",
                "properties": {"symbol_name": {"type": "string"}},
                "required": ["symbol_name"],
            },
            run=wrap(mcp_tools.find_usages, project=project),
            summarize=lambda a: a.get("symbol_name", ""),
        ),
        Tool(
            name="impact_analysis",
            description=(
                "Assess the blast radius of changing a symbol before you change it."
            ),
            parameters={
                "type": "object",
                "properties": {"symbol_name": {"type": "string"}},
                "required": ["symbol_name"],
            },
            run=wrap(mcp_tools.impact_analysis, project=project),
            summarize=lambda a: a.get("symbol_name", ""),
        ),
        Tool(
            name="explain_file",
            description="Compact structural summary of one file: its symbols and their roles.",
            parameters={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
            run=wrap(mcp_tools.explain_file, project=project),
            summarize=lambda a: a.get("file_path", ""),
        ),
        Tool(
            name="list_projects",
            description="List every indexed project this session can query.",
            parameters={"type": "object", "properties": {}},
            run=lambda: mcp_tools.list_projects(),
            summarize=lambda _a: "projects",
        ),
    ]


# ── filesystem tools ────────────────────────────────────────────────────────


def _read_file(ctx: ToolContext, path: str, offset: int = 0, limit: int = 2000) -> str:
    target = ctx.resolve(path)
    if not target.is_file():
        raise ToolError(f"Not a file: {path}")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"Not a text file: {path}")
    # Record what the model saw so edit_file can detect a stale write.
    ctx.read_hashes[str(target)] = _digest(text)
    lines = text.splitlines()
    window = lines[offset : offset + limit]
    numbered = "\n".join(
        f"{i + offset + 1}\t{line}" for i, line in enumerate(window)
    )
    if not numbered:
        return "(empty file)"
    suffix = ""
    if offset + limit < len(lines):
        suffix = f"\n... [{len(lines) - offset - limit} more lines]"
    return _truncate(numbered) + suffix


def _write_file(ctx: ToolContext, path: str, content: str) -> str:
    target = ctx.resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.is_file()
    target.write_text(content, encoding="utf-8")
    ctx.read_hashes[str(target)] = _digest(content)
    verb = "Updated" if existed else "Created"
    return f"{verb} {ctx.relative(target)} ({len(content.splitlines())} lines)"


def _edit_file(ctx: ToolContext, path: str, old_string: str, new_string: str,
               replace_all: bool = False) -> str:
    target = ctx.resolve(path)
    if not target.is_file():
        raise ToolError(f"Not a file: {path}")
    text = target.read_text(encoding="utf-8")
    known = ctx.read_hashes.get(str(target))
    if known is None:
        raise ToolError(
            f"Read {ctx.relative(target)} before editing it, so the edit applies to "
            "the current content."
        )
    if known != _digest(text):
        ctx.read_hashes.pop(str(target), None)
        raise ToolError(
            f"{ctx.relative(target)} changed on disk since you read it. Read it again "
            "and redo the edit against the new content."
        )
    count = text.count(old_string)
    if count == 0:
        raise ToolError("old_string was not found in the file.")
    if count > 1 and not replace_all:
        raise ToolError(
            f"old_string appears {count} times. Include more surrounding context to "
            "make it unique, or pass replace_all=true."
        )
    updated = text.replace(old_string, new_string) if replace_all else text.replace(
        old_string, new_string, 1
    )
    target.write_text(updated, encoding="utf-8")
    ctx.read_hashes[str(target)] = _digest(updated)
    replaced = count if replace_all else 1
    return f"Edited {ctx.relative(target)} ({replaced} replacement(s))"


_GLOB_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
              "build", ".next", "target", ".sema", ".mypy_cache", ".pytest_cache"}


def _glob(ctx: ToolContext, pattern: str, path: str = ".") -> str:
    base = ctx.resolve(path)
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _GLOB_SKIP]
        for name in filenames:
            full = Path(dirpath) / name
            rel = str(full.relative_to(base))
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
                matches.append(ctx.relative(full))
        if len(matches) > 500:
            break
    if not matches:
        return f"No files matching {pattern}"
    matches.sort()
    return "\n".join(matches[:500])


def _grep(ctx: ToolContext, pattern: str, path: str = ".", glob: str = "*") -> str:
    base = ctx.resolve(path)
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"Invalid regex: {exc}")
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _GLOB_SKIP]
        for name in filenames:
            if not fnmatch.fnmatch(name, glob):
                continue
            full = Path(dirpath) / name
            try:
                content = full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{ctx.relative(full)}:{number}: {line.strip()[:200]}")
                    if len(hits) >= 200:
                        return "\n".join(hits) + "\n... [more matches omitted]"
    return "\n".join(hits) if hits else f"No matches for {pattern}"


# ── shell ───────────────────────────────────────────────────────────────────


def command_binary(command: str) -> str:
    """First token of a shell command, for devops routing and prefix grants."""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return Path(parts[0]).name if parts else ""


def command_prefix(command: str) -> str:
    """Coarse grant key: the binary plus its first subcommand (`npm test`)."""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return " ".join(parts[:2]) if parts else command


def _bash(ctx: ToolContext, command: str, timeout: int = _BASH_TIMEOUT) -> str:
    binary = command_binary(command)
    if binary in DEVOPS_BINARIES:
        return _devops_exec(ctx, command)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(ctx.root),
            capture_output=True,
            text=True,
            timeout=min(timeout, 600),
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"Command timed out after {timeout}s: {command}")
    out = (proc.stdout or "") + (proc.stderr or "")
    status = "" if proc.returncode == 0 else f"\n[exit code {proc.returncode}]"
    return _truncate(out.strip() or "(no output)") + status


def _devops_exec(ctx: ToolContext, command: str) -> str:
    """Route kubectl/terraform/aws/helm through sema's analyze-first gate.

    The gate classifies the command, redacts secrets from its output, and queues
    anything destructive for explicit approval instead of running it.
    """
    from ..devops import gate

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ToolError(f"Could not parse command: {exc}")
    result = gate.run(argv, ctx.root)
    if result.get("status") == "needs_approval":
        return (
            f"BLOCKED — this command needs approval before it runs.\n"
            f"action_id: {result.get('action_id')}\n"
            f"reason: {result.get('reason')}\n"
            f"Tell the user to run `/devops approve {result.get('action_id')}`."
        )
    body = result.get("stdout") or result.get("output") or ""
    if result.get("stderr"):
        body += "\n" + result["stderr"]
    return _truncate(body.strip() or f"(devops: {result.get('status', 'ok')})")


# ── assembly ────────────────────────────────────────────────────────────────


def _coding_tools(ctx: ToolContext) -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description=(
                "Read a text file from the project. Returns numbered lines. You must "
                "read a file before editing it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root."},
                    "offset": {"type": "integer", "description": "First line to read (0-based).", "default": 0},
                    "limit": {"type": "integer", "description": "How many lines to read.", "default": 2000},
                },
                "required": ["path"],
            },
            run=lambda **kw: _read_file(ctx, **kw),
            summarize=lambda a: a.get("path", ""),
        ),
        Tool(
            name="write_file",
            description="Create a file or replace its entire contents.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            run=lambda **kw: _write_file(ctx, **kw),
            summarize=lambda a: a.get("path", ""),
        ),
        Tool(
            name="edit_file",
            description=(
                "Replace an exact string in a file. Read the file first. Fails if the "
                "file changed since you read it, or if old_string is ambiguous."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string", "description": "Exact text to replace."},
                    "new_string": {"type": "string", "description": "Replacement text."},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old_string", "new_string"],
            },
            run=lambda **kw: _edit_file(ctx, **kw),
            summarize=lambda a: a.get("path", ""),
        ),
        Tool(
            name="bash",
            description=(
                "Run a shell command in the project root. kubectl/terraform/aws/helm "
                "are routed through sema's devops approval gate."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": _BASH_TIMEOUT},
                },
                "required": ["command"],
            },
            run=lambda **kw: _bash(ctx, **kw),
            summarize=lambda a: a.get("command", ""),
        ),
        Tool(
            name="glob",
            description="List files matching a glob pattern.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["pattern"],
            },
            run=lambda **kw: _glob(ctx, **kw),
            summarize=lambda a: a.get("pattern", ""),
        ),
        Tool(
            name="grep",
            description="Regex search across file contents.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "glob": {"type": "string", "default": "*"},
                },
                "required": ["pattern"],
            },
            run=lambda **kw: _grep(ctx, **kw),
            summarize=lambda a: a.get("pattern", ""),
        ),
    ]


# Which tools each mode may use. Ask mode gets none: it is plain conversation.
READ_ONLY_TOOLS = {
    "search_code", "get_code", "check_reuse", "repo_map", "find_usages",
    "impact_analysis", "explain_file", "list_projects", "read_file", "glob", "grep",
}


def build_tools(ctx: ToolContext, mode: str = "agent", use_index: bool = True) -> list[Tool]:
    """The tool set for one mode."""
    if mode == "ask":
        return []
    tools = (_sema_tools(ctx) if use_index else []) + _coding_tools(ctx)
    if mode == "plan":
        return [t for t in tools if t.name in READ_ONLY_TOOLS]
    return tools


async def execute(tool: Tool, args: dict[str, Any], ctx: ToolContext) -> tuple[str, bool]:
    """Run one tool call through the permission gate.

    Returns ``(result_text, is_error)``. A denial is not an exception — it comes
    back as an ordinary result so the model can choose another approach.
    """
    prefix = None
    if tool.name == "bash":
        prefix = command_prefix(str(args.get("command", "")))
    request = ApprovalRequest(
        tool=tool.name,
        summary=tool.summary(args),
        detail=_render_args(args),
        prefix=prefix,
    )
    if not await ctx.permissions.check(request):
        return f"User declined the {tool.name} call.", True
    try:
        # Tools are synchronous and some are genuinely slow — a semantic search
        # loads the embedding model, bash blocks on a subprocess. Running them
        # on the event loop would freeze the UI, so hand them to a thread.
        return await asyncio.to_thread(tool.run, **args), False
    except ToolError as exc:
        return str(exc), True
    except TypeError as exc:
        return f"Invalid arguments for {tool.name}: {exc}", True
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, never fatal
        return f"{tool.name} failed: {type(exc).__name__}: {exc}", True


def _render_args(args: dict[str, Any]) -> str:
    lines = []
    for key, value in args.items():
        text = str(value)
        if len(text) > 400:
            text = text[:400] + " ..."
        lines.append(f"{key}: {text}")
    return "\n".join(lines)
