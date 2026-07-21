"""Slash command parsing and dispatch, driven by a fake app context."""

import asyncio
from pathlib import Path

import pytest

from sema.agent import ops
from sema.agent.session import Session, SessionStore
from sema.tui import commands


class FakeContext:
    """The AppContext slice commands are allowed to touch."""

    def __init__(self, root: Path, base_dir: Path):
        self.root = root
        self.store = SessionStore(base_dir, str(root))
        self.session = Session.create("anthropic", "claude-opus-4-8", "agent")
        self.watcher = ops.Watcher(root)
        self.use_index = False
        self.pending_attachments: list = []
        self._provider_id = "anthropic"
        self.cleared = False
        self.quit_requested = False
        # Scripted picker: what the next ctx.choose() call returns, plus a log
        # of what it was offered.
        self.next_choice: str | None = None
        self.choices_offered: list = []

    async def choose(self, title, options, current=None):
        self.choices_offered.append((title, [o.id for o in options], current))
        return self.next_choice

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def set_provider(self, provider_id: str) -> None:
        self._provider_id = provider_id
        self.session.provider = provider_id
        from sema.agent.providers import get_provider

        provider = get_provider(provider_id)
        if self.session.model not in {m.id for m in provider.models}:
            self.session.model = provider.default_model

    def set_model(self, model: str) -> None:
        self.session.model = model

    def set_mode(self, mode: str) -> None:
        self.session.mode = mode

    def set_effort(self, effort: str) -> None:
        self.session.effort = effort

    def new_session(self) -> None:
        self.session = Session.create(self._provider_id, self.session.model,
                                      self.session.mode)

    def load_session(self, session_id: str) -> bool:
        loaded = self.store.load(session_id)
        if loaded is None:
            return False
        self.session = loaded
        return True

    def clear_transcript(self) -> None:
        self.cleared = True

    def request_quit(self) -> None:
        self.quit_requested = True


@pytest.fixture
def ctx(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return FakeContext(root, tmp_path / "store")


def run(ctx, text):
    return asyncio.run(commands.dispatch(ctx, text))


# ── parsing ─────────────────────────────────────────────────────────────────


def test_plain_text_is_not_a_command():
    assert commands.parse("explain the auth flow") is None


def test_parse_splits_name_and_args():
    parsed = commands.parse("/search auth token")
    assert parsed.name == "search"
    assert parsed.args == "auth token"


def test_parse_is_case_insensitive():
    assert commands.parse("/HELP").name == "help"


def test_unknown_slash_text_falls_through_to_the_model():
    """So a user can paste `/usr/bin/foo` without it being swallowed."""
    assert commands.parse("/usr/bin/foo") is None
    assert commands.parse("/nonsense") is None


def test_bare_slash_is_not_a_command():
    assert commands.parse("/") is None


def test_aliases_resolve():
    assert commands.parse("/exit").name == "exit"
    assert commands.REGISTRY["exit"] is commands.REGISTRY["quit"]


def test_completions_filter_by_prefix():
    assert "/search" in commands.completions("/sea")
    assert commands.completions("hello") == []


def test_dispatch_returns_none_for_a_prompt(ctx):
    assert run(ctx, "just a prompt") is None


# ── coverage of the documented surface ──────────────────────────────────────


EXPECTED = {
    "help", "quit", "clear", "new", "sessions", "resume", "cost",
    "mode", "provider", "model", "effort",
    "search", "get", "reuse", "map", "usages", "impact", "explain", "projects",
    "index", "watch", "add", "rm", "files", "status", "doctor",
    "setup", "update", "manage", "plan", "redact", "devops", "attach", "tools",
}


def test_every_planned_command_exists():
    assert EXPECTED <= set(commands.REGISTRY)


def test_help_lists_every_command(ctx):
    body = run(ctx, "/help")
    for name in EXPECTED:
        assert f"/{name}" in body


# ── session & app commands ──────────────────────────────────────────────────


def test_quit_requests_exit(ctx):
    run(ctx, "/quit")
    assert ctx.quit_requested is True


def test_clear_clears_the_view(ctx):
    run(ctx, "/clear")
    assert ctx.cleared is True


def test_new_starts_a_fresh_session(ctx):
    first = ctx.session.id
    run(ctx, "/new")
    assert ctx.session.id != first


def test_sessions_lists_saved_rows(ctx):
    ctx.session.title = "Older work"
    ctx.store.save(ctx.session)
    body = run(ctx, "/sessions")
    assert ctx.session.id in body
    assert "Older work" in body


def test_sessions_handles_an_empty_store(ctx):
    assert "No saved sessions" in run(ctx, "/sessions")


def test_resume_loads_a_saved_session(ctx):
    ctx.store.save(ctx.session)
    saved_id = ctx.session.id
    run(ctx, "/new")
    body = run(ctx, f"/resume {saved_id}")
    assert ctx.session.id == saved_id
    assert "Resumed" in body


def test_resume_reports_a_missing_id(ctx):
    assert "No session" in run(ctx, "/resume ghost")


def test_resume_without_an_id_opens_a_picker(ctx):
    ctx.store.save(ctx.session)
    saved_id = ctx.session.id
    ctx.next_choice = saved_id
    run(ctx, "/new")
    body = run(ctx, "/resume")
    title, ids, _current = ctx.choices_offered[-1]
    assert "Resume" in title and saved_id in ids
    assert ctx.session.id == saved_id
    assert "Resumed" in body


def test_resume_picker_says_so_when_there_is_nothing_saved(ctx):
    assert "No saved sessions" in run(ctx, "/resume")


def test_cancelling_a_picker_changes_nothing(ctx):
    ctx.next_choice = None
    before = ctx.session.mode
    assert run(ctx, "/mode") == ""
    assert ctx.session.mode == before


def test_cost_reports_the_tally(ctx):
    ctx.session.usage.add(input_tokens=100, output_tokens=50, cost=0.25)
    body = run(ctx, "/cost")
    assert "100" in body and "50" in body and "$0.25" in body


def test_cost_shows_na_when_unknown(ctx):
    ctx.session.usage.add(input_tokens=10, output_tokens=5)
    assert "n/a" in run(ctx, "/cost")


# ── mode / provider / model / effort ────────────────────────────────────────


def test_mode_without_args_opens_a_picker(ctx):
    ctx.next_choice = "plan"
    body = run(ctx, "/mode")
    title, ids, current = ctx.choices_offered[-1]
    assert title == "Mode"
    assert ids == ["ask", "plan", "agent"]
    assert current == "agent"          # picker opens on the active mode
    assert ctx.session.mode == "plan"
    assert "plan" in body


def test_mode_with_an_argument_skips_the_picker(ctx):
    run(ctx, "/mode plan")
    assert ctx.session.mode == "plan"
    assert ctx.choices_offered == []


def test_mode_rejects_an_unknown_value(ctx):
    run(ctx, "/mode banana")
    assert ctx.session.mode == "agent"


def test_provider_without_args_opens_a_picker(ctx):
    ctx.next_choice = "openai"
    body = run(ctx, "/provider")
    title, ids, current = ctx.choices_offered[-1]
    assert title == "Provider"
    assert "claude-code" in ids and "anthropic" in ids and len(ids) == 10
    assert current == "anthropic"
    assert ctx.provider_id == "openai"
    assert "openai" in body


def test_provider_with_an_argument_skips_the_picker(ctx):
    run(ctx, "/provider openai")
    assert ctx.provider_id == "openai"
    assert ctx.choices_offered == []


def test_switching_provider_resets_an_incompatible_model(ctx):
    run(ctx, "/provider openai")
    assert ctx.session.model == "gpt-5.6-sol"


def test_provider_rejects_an_unknown_id(ctx):
    run(ctx, "/provider nope")
    assert ctx.provider_id == "anthropic"


def test_model_picker_offers_the_current_provider_catalog(ctx):
    ctx.next_choice = "claude-haiku-4-5"
    run(ctx, "/model")
    title, ids, current = ctx.choices_offered[-1]
    assert "Claude" in title
    assert "claude-sonnet-5" in ids
    assert current == "claude-opus-4-8"
    assert ctx.session.model == "claude-haiku-4-5"


def test_model_picker_follows_a_provider_switch(ctx):
    run(ctx, "/provider openai")
    ctx.next_choice = "gpt-5.6-luna"
    run(ctx, "/model")
    _title, ids, _current = ctx.choices_offered[-1]
    assert all(i.startswith("gpt-") for i in ids)
    assert ctx.session.model == "gpt-5.6-luna"


def test_model_with_an_argument_skips_the_picker(ctx):
    run(ctx, "/model claude-haiku-4-5")
    assert ctx.session.model == "claude-haiku-4-5"
    assert ctx.choices_offered == []


def test_effort_without_args_opens_a_picker(ctx):
    ctx.next_choice = "xhigh"
    run(ctx, "/effort")
    title, ids, current = ctx.choices_offered[-1]
    assert "effort" in title.lower()
    assert "xhigh" in ids
    assert current == "default"
    assert ctx.session.effort == "xhigh"


def test_effort_sets_a_valid_level(ctx):
    run(ctx, "/effort xhigh")
    assert ctx.session.effort == "xhigh"


def test_effort_rejects_an_invalid_level(ctx):
    run(ctx, "/effort turbo")
    assert ctx.session.effort == "default"


def test_effort_reports_when_a_model_has_none(ctx):
    run(ctx, "/model claude-haiku-4-5")
    assert "no effort control" in run(ctx, "/effort")


# ── argument-less usage messages ────────────────────────────────────────────


@pytest.mark.parametrize("command", ["search", "get", "reuse", "usages", "impact",
                                     "explain", "add", "rm", "attach", "redact"])
def test_commands_needing_an_argument_show_usage(ctx, command):
    body = run(ctx, f"/{command}")
    assert "Usage" in body


# ── plan / tools / devops ───────────────────────────────────────────────────


def test_plan_reports_when_there_is_none(ctx):
    assert "No plan yet" in run(ctx, "/plan")


def test_plan_shows_a_saved_artifact(ctx):
    from sema.agent.plan_artifact import save_plan

    artifact = save_plan(ctx.root, ctx.session.id, "T", "1. do the thing")
    ctx.session.plan_path = artifact.relative_path
    assert "do the thing" in run(ctx, "/plan")


def test_tools_reflects_the_current_mode(ctx):
    agent_body = run(ctx, "/tools")
    assert "write_file" in agent_body and "bash" in agent_body
    run(ctx, "/mode plan")
    plan_body = run(ctx, "/tools")
    assert "write_file" not in plan_body
    assert "read_file" in plan_body
    run(ctx, "/mode ask")
    assert "without tools" in run(ctx, "/tools")


def test_tools_shows_the_permission_policy(ctx):
    body = run(ctx, "/tools")
    assert "[allow]" in body and "[ask]" in body


def test_devops_pending_is_empty_on_a_clean_repo(ctx):
    assert "No commands awaiting approval" in run(ctx, "/devops pending")


def test_devops_defaults_to_pending(ctx):
    assert "No commands awaiting approval" in run(ctx, "/devops")


def test_devops_approve_without_an_id_shows_usage(ctx):
    assert "Usage" in run(ctx, "/devops approve")


def test_devops_rejects_an_unknown_action(ctx):
    assert "Usage" in run(ctx, "/devops frobnicate")


# ── attachments ─────────────────────────────────────────────────────────────


def test_attach_stages_a_file(ctx):
    target = ctx.root / "notes.md"
    target.write_text("# hi")
    body = run(ctx, f"/attach {target}")
    assert "notes.md" in body
    assert len(ctx.pending_attachments) == 1


def test_attach_reports_an_unreadable_file(ctx):
    assert "Could not attach" in run(ctx, "/attach /nonexistent/file.md")


# ── watch ───────────────────────────────────────────────────────────────────


def test_watch_status_reports_off(ctx):
    assert "off" in run(ctx, "/watch status")


def test_watch_off_when_not_running(ctx):
    assert "not running" in run(ctx, "/watch off")


def test_index_enables_the_semantic_tools_for_the_session(ctx, monkeypatch):
    """A session that started without an index must pick one up after /index."""
    from sema.agent import ops

    async def fake_index(root, reset=False, verbose=False):
        return ops.CommandResult(True, "indexed 3 files")

    monkeypatch.setattr(ops, "index", fake_index)
    assert ctx.use_index is False
    body = run(ctx, "/index")
    assert ctx.use_index is True
    assert "now available" in body


def test_index_failure_does_not_enable_the_tools(ctx, monkeypatch):
    from sema.agent import ops

    async def failing(root, reset=False, verbose=False):
        return ops.CommandResult(False, "boom")

    monkeypatch.setattr(ops, "index", failing)
    run(ctx, "/index")
    assert ctx.use_index is False


def test_files_renders_a_readable_table(ctx, monkeypatch):
    from sema.agent import ops

    async def fake_list(root):
        return ops.CommandResult(True, "", [
            {"file": "greet.ts", "language": "typescript",
             "chunks": [{"name": "greet"}, {"name": "other"}]},
        ])

    monkeypatch.setattr(ops, "list_files", fake_list)
    body = run(ctx, "/files")
    assert "greet.ts" in body
    assert "typescript · 2 symbol(s)" in body
    assert "chunks" not in body  # no raw dicts leaking through


# ── command-menu matching (pure) ────────────────────────────────────────────


def test_matches_returns_everything_for_a_bare_slash():
    assert len(commands.matches("/")) == len(set(c.name for c in commands._ORDER))


def test_matches_ranks_prefix_hits_above_substring_hits():
    names = [spec.name for spec in commands.matches("/se")]
    assert names[:3] == ["sessions", "search", "setup"]
    # `reuse` only contains "se"; it must come after the prefix matches.
    assert "reuse" in names and names.index("reuse") > 2


def test_matches_is_case_insensitive():
    assert [s.name for s in commands.matches("/MOD")] == ["mode", "model"]


def test_matches_stops_once_arguments_are_being_typed():
    assert commands.matches("/search auth") == []


def test_matches_ignores_plain_prompts():
    assert commands.matches("explain this") == []


def test_matches_empty_for_an_unknown_stem():
    assert commands.matches("/zzzz") == []


@pytest.mark.parametrize("text,expected", [
    ("/", True),
    ("/se", True),
    ("  /se", True),
    ("/search auth", False),
    ("plain prompt", False),
    ("/multi\nline", False),
    ("", False),
])
def test_is_command_prefix(text, expected):
    assert commands.is_command_prefix(text) is expected
