"""
End-to-end: a scripted model drives the real tool stack against a real index.

Only the network call is faked. The sema index, the tool implementations, the
permission gate, the session store, and the plan artifact are all genuine, so
this is the test that would catch a break in the wiring between them.
"""

import asyncio
import shutil

import pytest

from sema.agent.loop import (
    Agent,
    AgentConfig,
    ToolFinished,
    ToolStarted,
    TurnComplete,
)
from sema.agent.permissions import auto_allow, auto_deny
from sema.agent.session import Session, SessionStore
from sema.mcp.tools import init_tools

from .test_loop import FakeProvider


@pytest.fixture
def live_project(tmp_path, indexed_store, fixture_repo):
    """A writable copy of the example repo, wired to a real sema index."""
    store, embedder = indexed_store
    root = tmp_path / "repo"
    shutil.copytree(fixture_repo, root)
    init_tools(store, embedder, root)
    return root


def build(root, script, mode="agent", permissions=None):
    provider = FakeProvider(script)
    session = Session.create("fake", "fake-1", mode)
    config = AgentConfig(
        root=root,
        provider=provider,
        model="fake-1",
        mode=mode,
        permissions=permissions or auto_allow(),
        use_index=True,
    )
    return Agent(config, session), provider, session


def run(agent, text="go"):
    async def go():
        return [event async for event in agent.run_turn(text)]

    return asyncio.run(go())


def test_search_code_returns_real_index_results(live_project):
    agent, _, _ = build(live_project, [
        {"tools": [("search_code", {"query": "authentication"})]},
        {"text": "found it"},
    ])
    finished = [e for e in run(agent) if isinstance(e, ToolFinished)]
    assert finished[0].is_error is False
    assert len(finished[0].output.strip()) > 0


def test_search_then_get_code_then_edit(live_project):
    """The full navigate → inspect → change path the workflow prescribes."""
    target = live_project / "src" / "auth.ts"
    if not target.exists():
        target = next(live_project.rglob("*.ts"))
    relative = str(target.relative_to(live_project))
    original = target.read_text()
    first_line = original.splitlines()[0]

    agent, provider, session = build(live_project, [
        {"tools": [("search_code", {"query": "auth"})]},
        {"tools": [("read_file", {"path": relative})]},
        {"tools": [("edit_file", {"path": relative, "old_string": first_line,
                                  "new_string": "// touched by sema\n" + first_line})]},
        {"text": "Edited the file."},
    ])
    events = run(agent)

    names = [e.name for e in events if isinstance(e, ToolStarted)]
    assert names == ["search_code", "read_file", "edit_file"]
    assert all(not e.is_error for e in events if isinstance(e, ToolFinished))
    assert target.read_text().startswith("// touched by sema")
    assert provider.calls == 4
    assert [e for e in events if isinstance(e, TurnComplete)][0].text == "Edited the file."


def test_edit_without_reading_is_refused_end_to_end(live_project):
    target = next(live_project.rglob("*.ts"))
    relative = str(target.relative_to(live_project))
    before = target.read_text()
    agent, _, _ = build(live_project, [
        {"tools": [("edit_file", {"path": relative,
                                  "old_string": before.splitlines()[0],
                                  "new_string": "nope"})]},
        {"text": "I will read it first."},
    ])
    finished = [e for e in run(agent) if isinstance(e, ToolFinished)][0]
    assert finished.is_error is True
    assert "before editing" in finished.output
    assert target.read_text() == before


def test_path_escape_is_refused_end_to_end(live_project, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    agent, _, _ = build(live_project, [
        {"tools": [("read_file", {"path": "../secret.txt"})]},
        {"text": "blocked"},
    ])
    finished = [e for e in run(agent) if isinstance(e, ToolFinished)][0]
    assert finished.is_error is True
    assert "escapes the project root" in finished.output
    assert "classified" not in finished.output


def test_denied_write_leaves_the_repo_untouched(live_project):
    agent, _, _ = build(
        live_project,
        [{"tools": [("write_file", {"path": "evil.txt", "content": "x"})]},
         {"text": "ok"}],
        permissions=auto_deny(),
    )
    run(agent)
    assert not (live_project / "evil.txt").exists()


def test_plan_mode_cannot_write_but_saves_the_plan(live_project):
    agent, _, session = build(
        live_project,
        [{"tools": [("write_file", {"path": "should_not_exist.txt", "content": "x"})]},
         {"text": "# Plan\n1. change auth"}],
        mode="plan",
    )
    events = run(agent)
    finished = [e for e in events if isinstance(e, ToolFinished)][0]
    assert finished.is_error is True
    assert "Unknown tool: write_file" in finished.output
    assert not (live_project / "should_not_exist.txt").exists()
    plan = live_project / ".sema" / "plans" / f"{session.id}.md"
    assert plan.exists()
    assert "change auth" in plan.read_text()


def test_session_persists_and_resumes_across_stores(live_project, tmp_path):
    """Write a session, reopen the store, and continue the same transcript."""
    base = tmp_path / "chatstore"
    store = SessionStore(base, str(live_project))
    agent, _, session = build(live_project, [{"text": "first answer"}])
    run(agent, "first question")
    store.save(session)

    reopened = SessionStore(base, str(live_project)).load(session.id)
    assert reopened is not None
    assert [m.content for m in reopened.messages] == ["first question", "first answer"]
    assert reopened.usage.turns == 1


def test_repo_map_and_check_reuse_run_against_the_index(live_project):
    agent, _, _ = build(live_project, [
        {"tools": [("repo_map", {}), ("check_reuse", {"description": "a login helper"})]},
        {"text": "done"},
    ])
    finished = [e for e in run(agent) if isinstance(e, ToolFinished)]
    assert len(finished) == 2
    assert all(not e.is_error for e in finished)


def test_bash_runs_inside_the_project_root(live_project):
    agent, _, _ = build(live_project, [
        {"tools": [("bash", {"command": "pwd"})]},
        {"text": "ok"},
    ])
    finished = [e for e in run(agent) if isinstance(e, ToolFinished)][0]
    assert str(live_project.resolve()) in finished.output
