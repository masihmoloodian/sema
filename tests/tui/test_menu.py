"""The `/` command menu, driven with real keystrokes through Textual's pilot."""

import pytest

from sema.tui.app import ChatInput, CommandMenu, SemaChatApp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return SemaChatApp(root=root, base_dir=tmp_path / "store")


async def _typed(pilot, app, text):
    """Type `text` into a focused, empty input and settle the UI."""
    field = app.query_one("#input", ChatInput)
    field.focus()
    field.text = ""
    await pilot.pause()
    for char in text:
        await pilot.press("slash" if char == "/" else char)
    await pilot.pause()
    return field, app.query_one(CommandMenu)


async def test_menu_is_closed_until_a_slash_is_typed(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        menu = app.query_one(CommandMenu)
        assert menu.is_open is False
        _field, menu = await _typed(pilot, app, "hello")
        assert menu.is_open is False


async def test_slash_opens_the_full_command_list(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        _field, menu = await _typed(pilot, app, "/")
        assert menu.is_open is True
        assert menu.option_count > 30


async def test_typing_filters_the_menu(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        _field, menu = await _typed(pilot, app, "/mod")
        names = [menu.get_option_at_index(i).id for i in range(menu.option_count)]
        assert names == ["mode", "model"]


async def test_menu_closes_when_nothing_matches(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        _field, menu = await _typed(pilot, app, "/zzzz")
        assert menu.is_open is False


async def test_arrows_navigate_and_wrap(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        _field, menu = await _typed(pilot, app, "/mod")
        assert menu.selected_name() == "mode"
        await pilot.press("down")
        assert menu.selected_name() == "model"
        await pilot.press("down")  # wraps
        assert menu.selected_name() == "mode"
        await pilot.press("up")    # wraps back
        assert menu.selected_name() == "model"


async def test_enter_completes_instead_of_sending(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        field, menu = await _typed(pilot, app, "/mod")
        await pilot.press("enter")
        await pilot.pause()
        assert field.text == "/mode "
        assert menu.is_open is False
        # Nothing was submitted — the transcript has only the boot banner.
        assert not any(
            "/mode" in str(getattr(w, "_markdown", "")) for w in
            app.query_one("#transcript").children
        )


async def test_tab_completes_too(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        field, _menu = await _typed(pilot, app, "/mod")
        await pilot.press("tab")
        await pilot.pause()
        assert field.text == "/mode "


async def test_completion_of_a_no_argument_command_adds_no_space(app):
    """`/help` takes no argument, so completing it leaves no trailing space."""
    async with app.run_test() as pilot:
        await pilot.pause()
        field, _menu = await _typed(pilot, app, "/hel")
        await pilot.press("tab")
        await pilot.pause()
        assert field.text == "/help"


async def test_escape_dismisses_the_menu(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        field, menu = await _typed(pilot, app, "/mod")
        await pilot.press("escape")
        await pilot.pause()
        assert menu.is_open is False
        assert field.text == "/mod"  # the typed text survives


async def test_typing_arguments_keeps_the_menu_closed(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        field, menu = await _typed(pilot, app, "/mode")
        await pilot.press("tab")            # completes to "/mode "
        for char in "plan":
            await pilot.press(char)
        await pilot.pause()
        assert menu.is_open is False
        assert field.text == "/mode plan"


async def test_full_flow_runs_the_completed_command(app):
    """Type → complete → finish the argument → Enter actually dispatches."""
    async with app.run_test() as pilot:
        await pilot.pause()
        _field, _menu = await _typed(pilot, app, "/mod")
        await pilot.press("enter")          # partial -> completes to "/mode "
        for char in "plan":
            await pilot.press(char)
        await pilot.press("enter")          # arguments typed -> sends
        await pilot.pause()
        assert app.session.mode == "plan"


async def test_enter_sends_normally_when_the_menu_is_closed(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        field, menu = await _typed(pilot, app, "/cost")
        await pilot.press("escape")
        await pilot.press("enter")
        await pilot.pause()
        assert menu.is_open is False
        assert field.text == ""  # submitted and cleared


async def test_history_still_works_when_the_menu_is_closed(app):
    """Up must reach prompt history, not be swallowed by the menu."""
    async with app.run_test() as pilot:
        await pilot.pause()
        field = app.query_one("#input", ChatInput)
        field.sent_history = ["earlier prompt"]
        field._cursor = 1
        field.focus()
        await pilot.press("up")
        await pilot.pause()
        assert field.text == "earlier prompt"


async def test_menu_labels_carry_the_summary(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        _field, menu = await _typed(pilot, app, "/sea")
        label = str(menu.get_option_at_index(0).prompt)
        assert "/search" in label
        assert "search" in label.lower()


# ── the interactive picker (provider / model / mode / effort) ───────────────


async def _open_picker(pilot, app, command: str):
    """Type a command that opens a picker and wait for the screen."""
    field = app.query_one("#input", ChatInput)
    field.focus()
    field.text = command
    await pilot.pause()
    field.action_dismiss_menu()
    field.action_submit()
    for _ in range(6):
        await pilot.pause()
    from sema.tui.app import ChoiceScreen

    return app.screen if isinstance(app.screen, ChoiceScreen) else None


async def test_provider_command_opens_a_picker(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_picker(pilot, app, "/provider")
        assert screen is not None
        options = screen.query_one("#chooser-list")
        ids = [options.get_option_at_index(i).id for i in range(options.option_count)]
        assert ids[:2] == ["claude-code", "codex"]
        assert "anthropic" in ids


async def test_picker_opens_on_the_current_value(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_picker(pilot, app, "/provider")
        options = screen.query_one("#chooser-list")
        # Session starts on the default provider, so that row is preselected.
        assert options.get_option_at_index(options.highlighted).id == "claude-code"


async def test_arrows_and_enter_change_the_provider(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        await _open_picker(pilot, app, "/provider")
        await pilot.press("down")          # claude-code -> codex
        await pilot.press("enter")
        for _ in range(6):
            await pilot.pause()
        assert app.provider_id == "codex"
        assert app.session.model == "default"      # model follows the provider


async def test_escape_cancels_the_picker(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        before = app.provider_id
        await _open_picker(pilot, app, "/provider")
        await pilot.press("down")
        await pilot.press("escape")
        for _ in range(6):
            await pilot.pause()
        assert app.provider_id == before


async def test_mode_picker_changes_the_mode(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_picker(pilot, app, "/mode")
        options = screen.query_one("#chooser-list")
        assert options.get_option_at_index(options.highlighted).id == "agent"
        await pilot.press("up")            # agent -> plan
        await pilot.press("enter")
        for _ in range(6):
            await pilot.pause()
        assert app.session.mode == "plan"


async def test_model_picker_lists_the_provider_catalog(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_picker(pilot, app, "/model")
        options = screen.query_one("#chooser-list")
        ids = [options.get_option_at_index(i).id for i in range(options.option_count)]
        # Claude Code is the default provider; its catalog is CLI aliases.
        assert "opus" in ids and "sonnet" in ids


async def test_effort_picker_changes_effort(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        await _open_picker(pilot, app, "/effort")
        await pilot.press("down")          # default -> low
        await pilot.press("enter")
        for _ in range(6):
            await pilot.pause()
        assert app.session.effort == "low"


async def test_enter_runs_a_fully_typed_command_instead_of_recompleting(app):
    """`/mode` + Enter should open the mode picker, not re-complete to `/mode `."""
    async with app.run_test() as pilot:
        await pilot.pause()
        field, menu = await _typed(pilot, app, "/mode")
        assert menu.is_open is True          # the menu is showing a match
        await pilot.press("enter")
        for _ in range(6):
            await pilot.pause()
        assert field.text == ""              # submitted, not completed
        from sema.tui.app import ChoiceScreen
        assert isinstance(app.screen, ChoiceScreen)


async def test_enter_still_completes_a_partial_command(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        field, _menu = await _typed(pilot, app, "/mod")
        await pilot.press("enter")
        await pilot.pause()
        assert field.text == "/mode "        # partial -> completed


async def test_escape_on_a_lone_slash_clears_the_input(app):
    """Opened the menu by accident — Esc should leave a clean prompt."""
    async with app.run_test() as pilot:
        await pilot.pause()
        field, menu = await _typed(pilot, app, "/")
        await pilot.press("escape")
        await pilot.pause()
        assert menu.is_open is False
        assert field.text == ""


async def test_escape_keeps_a_partially_typed_command(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        field, _menu = await _typed(pilot, app, "/mod")
        await pilot.press("escape")
        await pilot.pause()
        assert field.text == "/mod"


async def test_plain_text_sends_as_a_prompt(app):
    """The core path: type words, press Enter, no menu involved."""
    async with app.run_test() as pilot:
        await pilot.pause()
        field, menu = await _typed(pilot, app, "hello there")
        assert menu.is_open is False
        await pilot.press("enter")
        for _ in range(4):
            await pilot.pause()
        assert field.text == ""
        assert field.sent_history == ["hello there"]
