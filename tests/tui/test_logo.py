"""The sema wordmark and the running indicator."""

import pytest

from sema.tui import logo
from sema.tui.app import RunningIndicator, SemaChatApp


# ── the wordmark ────────────────────────────────────────────────────────────


def test_full_lockup_renders_both_ribbons_and_the_word():
    art = logo.render(120)
    lines = art.splitlines()
    assert len(lines) == 4
    # Two ribbons: the mark repeats its slanted bar twice.
    assert art.count("▟██████▙") == 2
    # And the wordmark sits beside it, not under it.
    assert all(len(line) > logo.full_width() - 10 for line in lines)


def test_narrow_terminals_get_the_compact_mark():
    assert logo.render(20) == logo.COMPACT
    assert "sema" in logo.COMPACT


def test_the_switch_happens_at_the_measured_width():
    assert logo.render(logo.full_width()) != logo.COMPACT
    assert logo.render(logo.full_width() - 1) == logo.COMPACT


def test_render_has_no_trailing_whitespace():
    """Trailing spaces show up as stray background blocks in a themed pane."""
    for line in logo.render(120).splitlines():
        assert line == line.rstrip()


def test_spinner_frames_are_single_width_and_distinct():
    assert len(logo.SPINNER) >= 2
    assert len(set(logo.SPINNER)) == len(logo.SPINNER)
    assert all(len(frame) == 2 for frame in logo.SPINNER)


# ── the running indicator ───────────────────────────────────────────────────


pytestmark_async = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return SemaChatApp(root=root, base_dir=tmp_path / "store")


@pytest.mark.asyncio
async def test_banner_shows_the_logo_on_open(app):
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert "▟██████▙" in app.logo_text
        assert app.query_one("#transcript").children  # and it was mounted


@pytest.mark.asyncio
async def test_indicator_is_hidden_until_a_turn_starts(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(RunningIndicator).display is False


@pytest.mark.asyncio
async def test_indicator_shows_spinner_name_and_hint(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        indicator = app.query_one(RunningIndicator)
        indicator.start("thinking")
        await pilot.pause()
        text = indicator.label_text
        assert indicator.display is True
        assert "sema" in text
        assert "thinking" in text
        assert "ctrl+c to interrupt" in text
        assert any(frame in text for frame in logo.SPINNER)


@pytest.mark.asyncio
async def test_indicator_renders_without_blanking(app):
    """Regression: a method named `_render` shadows Textual's own and the
    widget renders as nothing at all."""
    async with app.run_test() as pilot:
        await pilot.pause()
        indicator = app.query_one(RunningIndicator)
        indicator.start("thinking")
        await pilot.pause()
        # Rendering the screen must not raise, and the line must have content.
        assert indicator.render_line(0).text.strip() != ""


@pytest.mark.asyncio
async def test_indicator_reports_the_running_tool_and_tokens(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        indicator = app.query_one(RunningIndicator)
        indicator.start("thinking")
        indicator.update_detail("search_code", 1234)
        await pilot.pause()
        text = indicator.label_text
        assert "search_code" in text
        assert "1,234 tokens" in text


@pytest.mark.asyncio
async def test_spinner_advances_on_each_tick(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        indicator = app.query_one(RunningIndicator)
        indicator.start()
        first = indicator.label_text
        indicator._tick()
        await pilot.pause()
        assert indicator.label_text != first


@pytest.mark.asyncio
async def test_stop_hides_it_and_cancels_the_timer(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        indicator = app.query_one(RunningIndicator)
        indicator.start()
        await pilot.pause()
        indicator.stop()
        await pilot.pause()
        assert indicator.display is False
        assert indicator._timer is None


@pytest.mark.asyncio
async def test_restarting_resets_the_token_count(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        indicator = app.query_one(RunningIndicator)
        indicator.start()
        indicator.update_detail("bash", 500)
        indicator.stop()
        indicator.start("thinking")
        await pilot.pause()
        assert indicator.tokens == 0
        assert "tokens" not in indicator.label_text
