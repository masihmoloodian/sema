"""The Textual app, driven headlessly with Textual's pilot."""

import asyncio


import pytest

from sema.agent.permissions import ALLOW, ALLOW_ALWAYS, DENY, ApprovalRequest
from sema.tui.app import ChatInput, SemaChatApp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "hello.txt").write_text("hi")
    return SemaChatApp(root=root, base_dir=tmp_path / "store")


async def test_app_boots_and_shows_the_banner(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#transcript")
        assert app.query_one("#status")
        # No index in the fixture, so the app must say so rather than fail.
        assert app.use_index is False


async def test_status_bar_reflects_mode_and_model(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "agent" in app.status_text
        # Default provider is claude-code, whose default model is "default".
        assert "claude-code" in app.status_text


async def test_slash_command_renders_without_calling_a_provider(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        reply = await app.handle_input("/help")
        assert reply is not None and "/search" in reply
        await pilot.pause()
        assert len(app.query_one("#transcript").children) > 0


async def test_slash_command_changes_app_state(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_input("/mode plan")
        assert app.session.mode == "plan"
        assert "plan" in app.status_text


async def test_cycle_mode_action_rotates(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.session.mode == "agent"
        app.action_cycle_mode()
        assert app.session.mode == "ask"
        app.action_cycle_mode()
        assert app.session.mode == "plan"
        app.action_cycle_mode()
        assert app.session.mode == "agent"


async def test_toggle_thinking(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.show_thinking is False
        app.action_toggle_thinking()
        assert app.show_thinking is True


async def test_clear_empties_the_transcript(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app._append("some content")
        app.action_clear()
        await pilot.pause()
        assert len(app.query_one("#transcript").children) == 0


async def test_new_session_replaces_the_id(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.session.id
        app.new_session()
        assert app.session.id != first


async def test_set_provider_resets_an_incompatible_model(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app.set_provider("openai")
        assert app.session.model == "gpt-5.6-sol"


async def test_load_session_replays_the_transcript(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        from sema.agent.session import ChatMessage

        app.session.messages.append(ChatMessage("user", "earlier question"))
        app.session.messages.append(ChatMessage("assistant", "earlier answer"))
        app.store.save(app.session)
        saved_id = app.session.id
        app.new_session()
        assert app.load_session(saved_id) is True
        await pilot.pause()
        assert len(app.query_one("#transcript").children) == 2


async def test_load_session_reports_a_missing_id(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.load_session("nope") is False


async def test_input_history_navigates(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        field = app.query_one("#input", ChatInput)
        field.sent_history = ["first", "second"]
        field._cursor = 2
        field.action_history_prev()
        assert field.text == "second"
        field.action_history_prev()
        assert field.text == "first"
        field.action_history_next()
        assert field.text == "second"


async def test_approval_screen_allow(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        request = ApprovalRequest(tool="bash", summary="npm test", prefix="npm test")
        decision = []

        async def push():
            decision.append(await app._ask_permission(request))

        app.run_worker(push(), name="approval")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert decision == [ALLOW]


async def test_approval_screen_deny_on_escape(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        request = ApprovalRequest(tool="write_file", summary="x.txt")
        decision = []

        async def push():
            decision.append(await app._ask_permission(request))

        app.run_worker(push(), name="approval")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert decision == [DENY]


async def test_approval_screen_always(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        request = ApprovalRequest(tool="bash", summary="ls", prefix="ls")
        decision = []

        async def push():
            decision.append(await app._ask_permission(request))

        app.run_worker(push(), name="approval")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert decision == [ALLOW_ALWAYS]


async def test_index_query_runs_off_the_event_loop(tmp_path, indexed_store):
    """A search must not block the UI thread — it loads the embedding model."""

    from sema.mcp.tools import init_tools

    store, embedder = indexed_store
    root = tmp_path / "live"
    root.mkdir()
    (root / ".sema" / "index").mkdir(parents=True)
    init_tools(store, embedder, root)

    app = SemaChatApp(root=root, base_dir=tmp_path / "store")
    async with app.run_test() as pilot:
        await pilot.pause()
        ticks = []

        async def heartbeat():
            for _ in range(20):
                ticks.append(1)
                await asyncio.sleep(0.01)

        beat = asyncio.create_task(heartbeat())
        reply = await app.handle_input("/search authentication")
        beat.cancel()

        assert reply is not None and "```" in reply
        # The loop kept running while the query worked.
        assert len(ticks) > 1


@pytest.mark.filterwarnings("ignore")
def test_silence_progress_bars_is_idempotent_and_safe():
    """Called on every app start; must never raise even if tqdm is odd."""
    from sema.agent import ops

    ops.silence_progress_bars()
    ops.silence_progress_bars()
    import os

    assert os.environ["TQDM_DISABLE"] == "1"
    assert os.environ["TOKENIZERS_PARALLELISM"] == "false"


# ── sticky preferences ──────────────────────────────────────────────────────


async def test_defaults_to_claude_code_on_a_first_run(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    app = SemaChatApp(root=root, base_dir=tmp_path / "store")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.provider_id == "claude-code"


async def test_provider_and_model_survive_a_restart(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    base = tmp_path / "store"

    first = SemaChatApp(root=root, base_dir=base)
    async with first.run_test() as pilot:
        await pilot.pause()
        first.set_provider("openai")
        first.set_model("gpt-5.6-luna")
        first.set_effort("high")
        first.set_mode("plan")
        await pilot.pause()

    second = SemaChatApp(root=root, base_dir=base)
    async with second.run_test() as pilot:
        await pilot.pause()
        assert second.provider_id == "openai"
        assert second.session.model == "gpt-5.6-luna"
        assert second.session.effort == "high"
        assert second.session.mode == "plan"


async def test_an_explicit_flag_overrides_the_saved_preference(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    base = tmp_path / "store"
    from sema.agent import prefs

    prefs.save(prefs.Prefs(provider="openai", model="gpt-5.6-luna"), base)

    app = SemaChatApp(root=root, provider_id="codex", base_dir=base)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.provider_id == "codex"
        # The saved model belongs to another provider, so it is not carried over.
        assert app.session.model == "default"


async def test_a_saved_model_from_another_provider_is_discarded(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    base = tmp_path / "store"
    from sema.agent import prefs

    prefs.save(prefs.Prefs(provider="anthropic", model="gpt-5.6-luna"), base)
    app = SemaChatApp(root=root, base_dir=base)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.session.model == "claude-opus-4-8"


async def test_an_unknown_saved_provider_falls_back(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    base = tmp_path / "store"
    from sema.agent import prefs

    prefs.save(prefs.Prefs(provider="retired-provider"), base)
    app = SemaChatApp(root=root, base_dir=base)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.provider_id == "claude-code"


# ── one-time CLI edit consent ───────────────────────────────────────────────


async def test_cli_agent_consent_is_asked_once_and_remembered(tmp_path):
    """The old behavior nagged every turn; now it is a single question."""
    from sema.tui.app import ApprovalScreen

    root = tmp_path / "repo"
    root.mkdir()
    app = SemaChatApp(root=root, base_dir=tmp_path / "store")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._cli_edit_consent is None

        async def ask():
            await app._ensure_cli_edit_consent()

        app.run_worker(ask(), name="consent1")
        await pilot.pause()
        assert isinstance(app.screen, ApprovalScreen)
        await pilot.press("y")
        for _ in range(4):
            await pilot.pause()
        assert app._cli_edit_consent is True
        assert app.permissions.bypass is True

        # A second turn must not ask again.
        app.run_worker(ask(), name="consent2")
        for _ in range(4):
            await pilot.pause()
        assert not isinstance(app.screen, ApprovalScreen)


async def test_denying_cli_consent_keeps_it_read_only(tmp_path):

    root = tmp_path / "repo"
    root.mkdir()
    app = SemaChatApp(root=root, base_dir=tmp_path / "store")
    async with app.run_test() as pilot:
        await pilot.pause()

        async def ask():
            await app._ensure_cli_edit_consent()

        app.run_worker(ask(), name="consent")
        await pilot.pause()
        await pilot.press("n")
        for _ in range(4):
            await pilot.pause()
        assert app._cli_edit_consent is False
        assert app.permissions.bypass is False


async def test_yes_flag_skips_the_consent_prompt(tmp_path):
    from sema.tui.app import ApprovalScreen

    root = tmp_path / "repo"
    root.mkdir()
    app = SemaChatApp(root=root, base_dir=tmp_path / "store", yes=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._ensure_cli_edit_consent()
        await pilot.pause()
        assert not isinstance(app.screen, ApprovalScreen)


async def test_no_consent_prompt_outside_agent_mode(tmp_path):
    from sema.tui.app import ApprovalScreen

    root = tmp_path / "repo"
    root.mkdir()
    app = SemaChatApp(root=root, mode="ask", base_dir=tmp_path / "store")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._ensure_cli_edit_consent()
        await pilot.pause()
        assert not isinstance(app.screen, ApprovalScreen)
        assert app._cli_edit_consent is None


async def test_switching_provider_asks_again(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    app = SemaChatApp(root=root, base_dir=tmp_path / "store")
    async with app.run_test() as pilot:
        await pilot.pause()
        app._cli_edit_consent = True
        app.set_provider("codex")
        assert app._cli_edit_consent is None


async def test_status_bar_shows_the_cached_share(tmp_path):
    """A big token count is mostly re-sent context; say so."""
    root = tmp_path / "repo"
    root.mkdir()
    app = SemaChatApp(root=root, base_dir=tmp_path / "store")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.session.usage.add(input_tokens=14_685, output_tokens=49, cached=9_984)
        app._refresh_status()
        assert "14,734 tok" in app.status_text
        assert "(9,984 cached)" in app.status_text


async def test_status_bar_omits_cached_when_there_is_none(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    app = SemaChatApp(root=root, base_dir=tmp_path / "store")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.session.usage.add(input_tokens=100, output_tokens=10)
        app._refresh_status()
        assert "cached" not in app.status_text
