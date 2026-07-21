"""
Slash commands.

Parsing and dispatch live here, separate from the Textual widgets, so the whole
command surface is testable without a terminal. Handlers receive an
``AppContext`` — the small slice of app state a command may touch — and return
Markdown to append to the transcript.

Every command routes to the same implementation the CLI uses (``agent.ops``);
none of them re-implement index or management logic.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from ..agent import ops
from ..agent.providers import PROVIDERS, get_provider
from ..agent.session import Session, SessionStore

MODES = ("ask", "plan", "agent")

MODE_HELP = {
    "ask": "Conversation only — no repository access",
    "plan": "Read-only investigation; saves a plan artifact",
    "agent": "Full tools, including edits and shell",
}


@dataclass
class Choice:
    """One row in an interactive picker."""

    id: str
    label: str
    description: str = ""
    recommended: bool = False


class AppContext(Protocol):
    """What a command may read and change."""

    root: Path
    session: Session
    store: SessionStore
    watcher: ops.Watcher
    use_index: bool
    # Files staged by /attach, consumed by the next turn.
    pending_attachments: list[Any]

    @property
    def provider_id(self) -> str: ...

    async def choose(
        self, title: str, options: list["Choice"], current: str | None = None
    ) -> str | None:
        """Present an arrow-navigable picker; returns the chosen id, or None."""
        ...

    def set_provider(self, provider_id: str) -> None: ...
    def set_model(self, model: str) -> None: ...
    def set_mode(self, mode: str) -> None: ...
    def set_effort(self, effort: str) -> None: ...
    def new_session(self) -> None: ...
    def load_session(self, session_id: str) -> bool: ...
    def clear_transcript(self) -> None: ...
    def request_quit(self) -> None: ...


Handler = Callable[[AppContext, str], Awaitable[str]]


@dataclass
class Command:
    name: str
    summary: str
    handler: Handler
    usage: str = ""
    aliases: tuple[str, ...] = ()


REGISTRY: dict[str, Command] = {}
_ORDER: list[Command] = []


def command(name: str, summary: str, usage: str = "",
            aliases: tuple[str, ...] = ()) -> Callable[[Handler], Handler]:
    def decorate(handler: Handler) -> Handler:
        spec = Command(name=name, summary=summary, handler=handler,
                       usage=usage or f"/{name}", aliases=aliases)
        REGISTRY[name] = spec
        for alias in aliases:
            REGISTRY[alias] = spec
        _ORDER.append(spec)
        return handler

    return decorate


@dataclass
class ParsedCommand:
    name: str
    args: str


def parse(text: str) -> ParsedCommand | None:
    """Split a slash command. Returns None for ordinary prompts.

    A line that starts with ``/`` but names no known command is treated as a
    prompt, so a user can type ``/usr/bin/foo`` without it being swallowed.
    """
    stripped = text.strip()
    if not stripped.startswith("/") or len(stripped) < 2:
        return None
    body = stripped[1:]
    name, _, args = body.partition(" ")
    name = name.lower()
    if name not in REGISTRY:
        return None
    return ParsedCommand(name=name, args=args.strip())


async def dispatch(ctx: AppContext, text: str) -> str | None:
    """Run a slash command, or return None if this is a normal prompt."""
    parsed = parse(text)
    if parsed is None:
        return None
    return await REGISTRY[parsed.name].handler(ctx, parsed.args)


def completions(prefix: str) -> list[str]:
    """Command names matching a `/pre` prefix, for inline completion."""
    return [f"/{spec.name}" for spec in matches(prefix)]


def matches(prefix: str) -> list[Command]:
    """Commands to offer for a partially-typed `/…`, best match first.

    Prefix hits rank above substring hits, so typing ``/se`` puts ``/search``
    ahead of ``/sessions``' sibling ``/setup`` while still surfacing it. Returns
    nothing once the user has typed past the command name (a space), since at
    that point they are writing arguments.
    """
    if not prefix.startswith("/"):
        return []
    stem = prefix[1:].strip().lower()
    if " " in prefix.strip()[1:]:
        return []
    if not stem:
        return list(_ORDER)
    starts = [s for s in _ORDER if s.name.startswith(stem)]
    contains = [s for s in _ORDER if stem in s.name and s not in starts]
    return starts + contains


def is_command_prefix(text: str) -> bool:
    """True while the user is still typing a command name."""
    stripped = text.lstrip()
    if not stripped.startswith("/"):
        return False
    return "\n" not in stripped and " " not in stripped


def _table(rows: list[tuple[str, str]]) -> str:
    """Render aligned two-column rows.

    Fenced rather than inline-code per line: Markdown reflows consecutive lines
    into one paragraph, which turns a column layout into a wall of text.
    """
    if not rows:
        return "_(none)_"
    width = max(len(left) for left, _ in rows)
    body = "\n".join(f"{left.ljust(width)}  {right}".rstrip() for left, right in rows)
    return f"```\n{body}\n```"


# ── session & app ───────────────────────────────────────────────────────────


@command("help", "Show every command")
async def _help(_ctx: AppContext, _args: str) -> str:
    rows = [(spec.usage, spec.summary) for spec in _ORDER]
    return "**Commands**\n\n" + _table(rows)


@command("quit", "Exit sema chat", aliases=("exit",))
async def _quit(ctx: AppContext, _args: str) -> str:
    ctx.request_quit()
    return "Bye."


@command("clear", "Clear the transcript view (keeps the session)")
async def _clear(ctx: AppContext, _args: str) -> str:
    ctx.clear_transcript()
    return ""


@command("new", "Start a fresh session")
async def _new(ctx: AppContext, _args: str) -> str:
    ctx.new_session()
    return f"New session `{ctx.session.id}`."


@command("sessions", "List saved sessions")
async def _sessions(ctx: AppContext, _args: str) -> str:
    rows = ctx.store.list()
    if not rows:
        return "No saved sessions yet."
    return "**Sessions**\n\n" + _table([
        (row.id, f"{row.title} — {row.provider}/{row.model}, {row.message_count} msgs")
        for row in rows[:30]
    ])


@command("resume", "Pick a session to resume", usage="/resume [id]")
async def _resume(ctx: AppContext, args: str) -> str:
    if not args:
        rows = ctx.store.list()
        if not rows:
            return "No saved sessions yet."
        picked = await ctx.choose(
            "Resume a session",
            [
                Choice(row.id, row.title,
                       f"{row.provider}/{row.model} · {row.message_count} messages")
                for row in rows[:50]
            ],
            current=ctx.session.id,
        )
        if picked is None:
            return ""
        if not ctx.load_session(picked):
            return f"No session `{picked}`."
        return f"Resumed `{ctx.session.id}` — {len(ctx.session.messages)} messages."
    if not ctx.load_session(args.strip()):
        return f"No session `{args.strip()}`."
    return f"Resumed `{ctx.session.id}` — {len(ctx.session.messages)} messages."


@command("cost", "Token and cost tally for this session")
async def _cost(ctx: AppContext, _args: str) -> str:
    usage = ctx.session.usage
    cost = f"${usage.cost:.4f}" if usage.cost_known else "n/a"
    return _table([
        ("turns", str(usage.turns)),
        ("input", f"{usage.input:,} ({usage.cached:,} cached)"),
        ("output", f"{usage.output:,}"),
        ("cost", cost),
    ])


# ── model & mode ────────────────────────────────────────────────────────────


@command("mode", "Pick the mode: ask | plan | agent", usage="/mode [ask|plan|agent]")
async def _mode(ctx: AppContext, args: str) -> str:
    if not args:
        picked = await ctx.choose(
            "Mode",
            [Choice(m, m, MODE_HELP[m], recommended=m == "agent") for m in MODES],
            current=ctx.session.mode,
        )
        if picked is None:
            return ""
        ctx.set_mode(picked)
        return f"Mode set to **{picked}**."
    choice = args.strip().lower()
    if choice not in MODES:
        return f"Unknown mode `{choice}`. Options: {', '.join(MODES)}."
    ctx.set_mode(choice)
    return f"Mode set to **{choice}**."


@command("provider", "Pick the provider", usage="/provider [id]")
async def _provider(ctx: AppContext, args: str) -> str:
    if not args:
        picked = await ctx.choose(
            "Provider",
            [
                Choice(
                    p.id,
                    p.label,
                    "uses your local login" if not p.requires_key
                    else f"needs {p.key_env}",
                    recommended=p.id == "anthropic",
                )
                for p in PROVIDERS
            ],
            current=ctx.provider_id,
        )
        if picked is None:
            return ""
        ctx.set_provider(picked)
        return f"Provider set to **{picked}** (model `{ctx.session.model}`)."
    choice = args.strip().lower()
    if choice not in {p.id for p in PROVIDERS}:
        return f"Unknown provider `{choice}`."
    ctx.set_provider(choice)
    return f"Provider set to **{choice}** (model `{ctx.session.model}`)."


@command("model", "Pick the model", usage="/model [id]")
async def _model(ctx: AppContext, args: str) -> str:
    provider = get_provider(ctx.provider_id)
    if not args:
        picked = await ctx.choose(
            f"Model — {provider.label}",
            [
                Choice(m.id, m.label, m.description, recommended=m.recommended)
                for m in provider.models
            ],
            current=ctx.session.model,
        )
        if picked is None:
            return ""
        ctx.set_model(picked)
        return f"Model set to **{picked}**."
    ctx.set_model(args.strip())
    return f"Model set to **{ctx.session.model}**."


@command("effort", "Pick the reasoning effort", usage="/effort [level]")
async def _effort(ctx: AppContext, args: str) -> str:
    provider = get_provider(ctx.provider_id)
    levels = provider.efforts_for(ctx.session.model)
    if not levels:
        return f"`{ctx.session.model}` has no effort control."
    if not args:
        picked = await ctx.choose(
            "Reasoning effort",
            [
                Choice(level, level,
                       "provider default" if level == "default" else "",
                       recommended=level == "high")
                for level in levels
            ],
            current=ctx.session.effort,
        )
        if picked is None:
            return ""
        ctx.set_effort(picked)
        return f"Effort set to **{picked}**."
    choice = args.strip().lower()
    if choice not in levels:
        return f"Unknown effort `{choice}`. Options: {', '.join(levels)}."
    ctx.set_effort(choice)
    return f"Effort set to **{choice}**."


# ── index queries ───────────────────────────────────────────────────────────


@command("search", "Semantic code search", usage="/search <query>")
async def _search(ctx: AppContext, args: str) -> str:
    if not args:
        return "Usage: `/search <query>`"
    return _fenced(await asyncio.to_thread(ops.search, args))


@command("get", "Show the source of a symbol", usage="/get <symbol>")
async def _get(_ctx: AppContext, args: str) -> str:
    if not args:
        return "Usage: `/get <symbol>`"
    return _fenced(await asyncio.to_thread(ops.get_code, args))


@command("reuse", "Does this already exist?", usage="/reuse <description>")
async def _reuse(_ctx: AppContext, args: str) -> str:
    if not args:
        return "Usage: `/reuse <description>`"
    return _fenced(await asyncio.to_thread(ops.reuse, args))


@command("map", "Repository architecture map")
async def _map(_ctx: AppContext, _args: str) -> str:
    return _fenced(await asyncio.to_thread(ops.repo_map))


@command("usages", "Find references to a symbol", usage="/usages <symbol>")
async def _usages(_ctx: AppContext, args: str) -> str:
    if not args:
        return "Usage: `/usages <symbol>`"
    return _fenced(await asyncio.to_thread(ops.find_usages, args))


@command("impact", "Blast radius of changing a symbol", usage="/impact <symbol>")
async def _impact(_ctx: AppContext, args: str) -> str:
    if not args:
        return "Usage: `/impact <symbol>`"
    return _fenced(await asyncio.to_thread(ops.impact, args))


@command("explain", "Summarize one file", usage="/explain <path>")
async def _explain(_ctx: AppContext, args: str) -> str:
    if not args:
        return "Usage: `/explain <path>`"
    return _fenced(await asyncio.to_thread(ops.explain, args))


@command("projects", "List indexed projects")
async def _projects(_ctx: AppContext, _args: str) -> str:
    return _fenced(await asyncio.to_thread(ops.list_projects))


# ── index management ────────────────────────────────────────────────────────


@command("index", "Build or rebuild the index", usage="/index [--reset]")
async def _index(ctx: AppContext, args: str) -> str:
    reset = "--reset" in args or args.strip() == "reset"
    result = await ops.index(ctx.root, reset=reset)
    body = _fenced(result.output or "Indexing finished.")
    if result.ok and not ctx.use_index:
        # The session started without an index, so the sema tools were withheld.
        # Now that one exists, enable them for the next turn.
        ctx.use_index = True
        body += "\n\n_Semantic tools are now available to the agent._"
    return body


@command("watch", "Toggle auto-indexing", usage="/watch [on|off|status]")
async def _watch(ctx: AppContext, args: str) -> str:
    choice = args.strip().lower()
    if choice == "status":
        return f"Watch is **{'on' if ctx.watcher.running else 'off'}**."
    if choice == "on":
        return await ctx.watcher.start()
    if choice == "off":
        return await ctx.watcher.stop()
    return await ctx.watcher.toggle()


@command("add", "Index a single file", usage="/add <file>")
async def _add(ctx: AppContext, args: str) -> str:
    if not args:
        return "Usage: `/add <file>`"
    result = await ops.add_file(ctx.root, args.strip())
    return _fenced(result.output)


@command("rm", "Drop a file from the index", usage="/rm <file>")
async def _rm(ctx: AppContext, args: str) -> str:
    if not args:
        return "Usage: `/rm <file>`"
    result = await ops.remove_file(ctx.root, args.strip())
    return _fenced(result.output)


@command("files", "List indexed files")
async def _files(ctx: AppContext, _args: str) -> str:
    result = await ops.list_files(ctx.root)
    payload = result.data
    if isinstance(payload, dict):
        payload = payload.get("files", [])
    if isinstance(payload, list) and payload:
        rows = []
        for entry in payload[:400]:
            if isinstance(entry, dict):
                name = entry.get("file", "?")
                language = entry.get("language", "")
                count = len(entry.get("chunks") or [])
                rows.append((name, f"{language} · {count} symbol(s)"))
            else:
                rows.append((str(entry), ""))
        return f"**{len(payload)} indexed file(s)**\n\n" + _table(rows)
    return _fenced(result.output)


@command("status", "Index and registration status")
async def _status(ctx: AppContext, _args: str) -> str:
    result = await ops.status(ctx.root)
    if result.data is not None:
        return _fenced(json.dumps(result.data, indent=2))
    return _fenced(result.output)


@command("doctor", "Run environment diagnostics")
async def _doctor(ctx: AppContext, _args: str) -> str:
    result = await ops.doctor(ctx.root)
    return _fenced(result.output)


@command("setup", "Register sema with every detected AI CLI",
         usage="/setup [uninstall|claude|codex|cursor|grok]")
async def _setup(ctx: AppContext, args: str) -> str:
    choice = args.strip().lower()
    if choice in ("claude", "codex", "cursor", "grok"):
        result = await ops.init_client(ctx.root, choice)
    else:
        result = await ops.setup(ctx.root, uninstall=choice == "uninstall")
    return _fenced(result.output)


@command("update", "Update the agent CLIs or sema itself", usage="/update [agents|sema|check]")
async def _update(_ctx: AppContext, args: str) -> str:
    choice = args.strip().lower()
    if choice == "sema":
        result = await ops.self_update()
    else:
        result = await ops.update_agents(check=choice == "check")
    return _fenced(result.output)


@command("manage", "Index health at a glance")
async def _manage(ctx: AppContext, _args: str) -> str:
    result = await ops.status(ctx.root)
    watch = "on" if ctx.watcher.running else "off"
    rows = [
        ("root", str(ctx.root)),
        ("index", "present" if ops.has_index(ctx.root) else "missing"),
        ("watch", watch),
        ("session", ctx.session.id),
    ]
    body = _table(rows)
    if result.data is not None:
        body += "\n\n" + _fenced(json.dumps(result.data, indent=2))
    return body


# ── plan, redaction, devops ─────────────────────────────────────────────────


@command("plan", "Show the session plan artifact", usage="/plan")
async def _plan(ctx: AppContext, _args: str) -> str:
    if not ctx.session.plan_path:
        return "No plan yet. Switch to `/mode plan` and describe the task."
    from ..agent.plan_artifact import read_plan

    body = read_plan(ctx.root, ctx.session.plan_path)
    return body or f"Plan file is missing: `{ctx.session.plan_path}`"


@command("redact", "Preview PII redaction of a string", usage="/redact <text>")
async def _redact(_ctx: AppContext, args: str) -> str:
    if not args:
        return "Usage: `/redact <text>`"
    clean, entities = await asyncio.to_thread(ops.redact_text, args)
    summary = ", ".join(
        f"{e.get('type')}×{e.get('count')}" for e in entities
    ) or "no entities found"
    return f"{_fenced(clean)}\n\n_{summary}_"


@command("devops", "Inspect the devops approval gate",
         usage="/devops pending|approve <id>|deny <id>|log")
async def _devops(ctx: AppContext, args: str) -> str:
    action, _, rest = args.strip().partition(" ")
    action = action.lower() or "pending"
    rest = rest.strip()
    if action == "pending":
        rows = ops.devops_pending(ctx.root)
        if not rows:
            return "No commands awaiting approval."
        return _table([
            (str(r.get("id", "?")), f"{r.get('command', '')} — {r.get('reason', '')}")
            for r in rows
        ])
    if action == "approve":
        if not rest:
            return "Usage: `/devops approve <id>`"
        return _fenced(json.dumps(ops.devops_approve(ctx.root, rest), indent=2))
    if action == "deny":
        if not rest:
            return "Usage: `/devops deny <id>`"
        return _fenced(json.dumps(ops.devops_deny(ctx.root, rest), indent=2))
    if action == "log":
        return _fenced(json.dumps(ops.devops_log(ctx.root), indent=2))
    return "Usage: `/devops pending|approve <id>|deny <id>|log`"


@command("attach", "Attach a file to the next message", usage="/attach <path>")
async def _attach(ctx: AppContext, args: str) -> str:
    if not args:
        return "Usage: `/attach <path>`"
    from ..agent import attachments as att

    directory = ctx.store.attachments_dir(ctx.session.id)
    try:
        staged = att.stage(directory, Path(args.strip()))
    except ValueError as exc:
        return f"Could not attach: {exc}"
    ctx.pending_attachments.append(staged)
    return f"Attached **{staged.name}** ({att.format_size(staged.size)}, {staged.kind})."


@command("tools", "List the tools available in the current mode")
async def _tools(ctx: AppContext, _args: str) -> str:
    from ..agent.permissions import PermissionManager, Policy, default_policies
    from ..agent.tools import ToolContext, build_tools

    context = ToolContext(root=ctx.root, permissions=PermissionManager())
    tools = build_tools(context, ctx.session.mode, ctx.use_index)
    if not tools:
        return f"Mode **{ctx.session.mode}** runs without tools."
    policies = default_policies()
    rows = []
    for tool in tools:
        policy = policies.get(tool.name, Policy.ASK).value
        headline = tool.description.split(".")[0]
        rows.append((tool.name, f"[{policy}] {headline}"))
    return _table(rows)


def _fenced(text: str) -> str:
    body = (text or "").strip()
    if not body:
        return "_(no output)_"
    return f"```\n{body}\n```"
