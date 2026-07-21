"""The agent loop, driven by a scripted fake provider.

The fake replaces only the network call, so the loop, the tool dispatch, the
permission gate, and session bookkeeping are all exercised for real.
"""

import asyncio

import pytest

from sema.agent.loop import (
    MAX_ITERATIONS,
    Agent,
    AgentConfig,
    Notice,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnComplete,
    collect,
)
from sema.agent.permissions import auto_allow, auto_deny
from sema.agent.providers.base import BaseProvider, ToolCall, TurnEnd, Usage
from sema.agent.session import Session


class FakeProvider(BaseProvider):
    """Replays a scripted list of turns, one per stream() call."""

    id = "fake"
    label = "Fake"
    requires_key = False
    reads_workspace = False
    default_model = "fake-1"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen_systems = []
        self.seen_tools = []

    async def stream(self, request):
        self.calls += 1
        self.seen_systems.append(request.system)
        self.seen_tools.append([t.name for t in request.tools])
        turn = self.script.pop(0) if self.script else {"text": "done"}
        for chunk in turn.get("chunks", [turn.get("text", "")]):
            if chunk:
                yield TextDelta(chunk)
        yield Usage(input_tokens=turn.get("input", 10),
                    output_tokens=turn.get("output", 5),
                    cost_usd=turn.get("cost"))
        calls = [
            ToolCall(id=f"c{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(turn.get("tools", []))
        ]
        yield TurnEnd(tool_calls=calls,
                      stop_reason="tool_use" if calls else "end_turn")

    def add_tool_results(self, request, results):
        request.scratch.append({"role": "user", "results": results})

    def assistant_text_only(self, request, text):
        request.scratch.append({"role": "assistant", "content": text})


@pytest.fixture
def project(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    return tmp_path


def make_agent(project, script, mode="agent", permissions=None):
    provider = FakeProvider(script)
    session = Session.create("fake", "fake-1", mode)
    config = AgentConfig(
        root=project,
        provider=provider,
        model="fake-1",
        mode=mode,
        permissions=permissions or auto_allow(),
        use_index=False,  # no real index in the fixture
    )
    return Agent(config, session), provider, session


def run(agent, text="hi"):
    async def go():
        events = []
        async for event in agent.run_turn(text):
            events.append(event)
        return events

    return asyncio.run(go())


def test_simple_turn_produces_text_and_completes(project):
    agent, provider, session = make_agent(project, [{"text": "hello there"}])
    events = run(agent)
    final = [e for e in events if isinstance(e, TurnComplete)][0]
    assert final.text == "hello there"
    assert provider.calls == 1
    assert [m.role for m in session.messages] == ["user", "assistant"]
    assert session.messages[1].content == "hello there"


def test_streamed_chunks_are_yielded_in_order(project):
    agent, _, _ = make_agent(project, [{"chunks": ["a", "b", "c"]}])
    deltas = [e.text for e in run(agent) if isinstance(e, TextDelta)]
    assert deltas == ["a", "b", "c"]


def test_tool_call_executes_then_loops(project):
    agent, provider, _ = make_agent(project, [
        {"text": "reading. ", "tools": [("read_file", {"path": "app.py"})]},
        {"text": "done"},
    ])
    events = run(agent)
    started = [e for e in events if isinstance(e, ToolStarted)]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert [e.name for e in started] == ["read_file"]
    assert finished[0].is_error is False
    assert "x = 1" in finished[0].output
    assert provider.calls == 2
    assert [e for e in events if isinstance(e, TurnComplete)][0].text == "reading. done"


def test_tool_write_actually_changes_the_file(project):
    agent, _, _ = make_agent(project, [
        {"tools": [("write_file", {"path": "new.txt", "content": "made it\n"})]},
        {"text": "written"},
    ])
    run(agent)
    assert (project / "new.txt").read_text() == "made it\n"


def test_denied_tool_still_completes_the_turn(project):
    agent, provider, _ = make_agent(
        project,
        [{"tools": [("write_file", {"path": "nope.txt", "content": "x"})]},
         {"text": "understood, I will not write"}],
        permissions=auto_deny(),
    )
    events = run(agent)
    finished = [e for e in events if isinstance(e, ToolFinished)][0]
    assert finished.is_error is True
    assert "declined" in finished.output
    assert not (project / "nope.txt").exists()
    assert [e for e in events if isinstance(e, TurnComplete)][0].text.endswith("not write")


def test_unknown_tool_is_reported_to_the_model(project):
    """A hallucinated tool name must come back as a result, not crash the turn."""
    agent, provider, _ = make_agent(project, [
        {"tools": [("nonexistent_tool", {})]},
        {"text": "ok, using a real one instead"},
    ])
    events = run(agent)
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert len(finished) == 1
    assert finished[0].is_error is True
    assert "Unknown tool: nonexistent_tool" in finished[0].output
    assert "read_file" in finished[0].output  # tells the model what does exist
    assert provider.calls == 2
    assert [e for e in events if isinstance(e, TurnComplete)][0].text.endswith("instead")


def test_parallel_tool_calls_all_execute(project):
    agent, _, _ = make_agent(project, [
        {"tools": [("read_file", {"path": "app.py"}),
                   ("glob", {"pattern": "*.py"})]},
        {"text": "both done"},
    ])
    names = [e.name for e in run(agent) if isinstance(e, ToolStarted)]
    assert names == ["read_file", "glob"]


def test_usage_accumulates_across_iterations(project):
    agent, _, session = make_agent(project, [
        {"tools": [("glob", {"pattern": "*"})], "input": 100, "output": 20, "cost": 0.01},
        {"text": "end", "input": 150, "output": 30, "cost": 0.02},
    ])
    final = [e for e in run(agent) if isinstance(e, TurnComplete)][0]
    assert final.usage.input_tokens == 250
    assert final.usage.output_tokens == 50
    assert final.usage.cost_usd == pytest.approx(0.03)
    assert session.usage.turns == 1  # one user turn, not one per API call
    assert session.usage.input == 250


def test_iteration_cap_stops_a_runaway_model(project):
    script = [{"tools": [("glob", {"pattern": "*"})]} for _ in range(MAX_ITERATIONS + 5)]
    agent, provider, _ = make_agent(project, script)
    events = run(agent)
    assert provider.calls == MAX_ITERATIONS
    assert any("Stopped after" in e.text for e in events if isinstance(e, Notice))


def test_ask_mode_offers_no_tools(project):
    agent, provider, _ = make_agent(project, [{"text": "chat only"}], mode="ask")
    run(agent)
    assert provider.seen_tools[0] == []


def test_plan_mode_offers_only_read_only_tools(project):
    agent, provider, _ = make_agent(project, [{"text": "# Plan\n1. do it"}], mode="plan")
    run(agent)
    assert "write_file" not in provider.seen_tools[0]
    assert "read_file" in provider.seen_tools[0]


def test_plan_mode_writes_the_artifact(project):
    agent, _, session = make_agent(project, [{"text": "# Plan\n1. step one"}], mode="plan")
    final = [e for e in run(agent) if isinstance(e, TurnComplete)][0]
    assert final.plan_path == ".sema/plans/" + session.id + ".md"
    body = (project / final.plan_path).read_text()
    assert "step one" in body
    assert session.plan_path == final.plan_path


def test_agent_mode_does_not_write_a_plan(project):
    agent, _, _ = make_agent(project, [{"text": "changed it"}], mode="agent")
    final = [e for e in run(agent) if isinstance(e, TurnComplete)][0]
    assert final.plan_path is None
    assert not (project / ".sema" / "plans").exists()


def test_system_prompt_carries_the_sema_workflow(project):
    agent, provider, _ = make_agent(project, [{"text": "x"}])
    agent.config.use_index = True
    run(agent)
    assert "search_code" in provider.seen_systems[0]


def test_existing_plan_is_injected_on_later_turns(project):
    agent, provider, session = make_agent(project, [{"text": "# Plan\nstep"}], mode="plan")
    run(agent)
    agent.config.provider = FakeProvider([{"text": "ok"}])
    run(agent, "now do it")
    assert "step" in agent.config.provider.seen_systems[0]


def test_collect_returns_the_completion(project):
    agent, _, _ = make_agent(project, [{"text": "final"}])
    result = asyncio.run(collect(agent.run_turn("go")))
    assert isinstance(result, TurnComplete)
    assert result.text == "final"


def test_cli_agent_without_bypass_warns_that_edits_will_not_apply(project):
    """These CLIs cannot be asked for consent under -p, so say so up front."""

    class FakeCli(FakeProvider):
        id = "fake-cli"
        label = "Fake CLI"
        reads_workspace = True

    from sema.agent.permissions import PermissionManager, default_policies

    provider = FakeCli([{"text": "ok"}])
    session = Session.create("fake-cli", "fake-1", "agent")
    agent = Agent(
        # asker=None means headless: nothing can answer a consent prompt.
        AgentConfig(root=project, provider=provider, model="fake-1", mode="agent",
                    permissions=PermissionManager(policies=default_policies()),
                    use_index=False),
        session,
    )
    notices = [e.text for e in run(agent) if isinstance(e, Notice)]
    assert any("cannot ask for consent" in n for n in notices)


def test_cli_agent_warning_is_suppressed_when_a_ui_can_ask(project):
    """A UI asks once up front, so repeating the warning every turn is noise."""

    class FakeCli(FakeProvider):
        id = "fake-cli"
        reads_workspace = True

    from sema.agent.permissions import PermissionManager, default_policies

    async def asker(_request):
        return "deny"

    manager = PermissionManager(policies=default_policies(), asker=asker)
    agent = Agent(
        AgentConfig(root=project, provider=FakeCli([{"text": "ok"}]), model="fake-1",
                    mode="agent", permissions=manager, use_index=False),
        Session.create("fake-cli", "fake-1", "agent"),
    )
    notices = [e.text for e in run(agent) if isinstance(e, Notice)]
    assert not any("cannot ask for consent" in n for n in notices)


def test_cli_agent_with_bypass_does_not_warn(project):
    class FakeCli(FakeProvider):
        id = "fake-cli"
        reads_workspace = True

    provider = FakeCli([{"text": "ok"}])
    agent = Agent(
        AgentConfig(root=project, provider=provider, model="fake-1", mode="agent",
                    permissions=auto_allow(), use_index=False),
        Session.create("fake-cli", "fake-1", "agent"),
    )
    notices = [e.text for e in run(agent) if isinstance(e, Notice)]
    assert not any("cannot ask for consent" in n for n in notices)


def test_permission_mode_reaches_the_provider(project):
    agent, provider, _ = make_agent(project, [{"text": "x"}], permissions=auto_allow())

    captured = {}
    original = provider.stream

    async def spy(request):
        captured["mode"] = request.permission_mode
        async for event in original(request):
            yield event

    provider.stream = spy
    run(agent)
    assert captured["mode"] == "bypass"
