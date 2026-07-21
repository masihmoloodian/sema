"""
The sema terminal app.

Layout: transcript on top, multiline input below, status bar at the bottom.
The agent runs in a Textual worker and posts events back to the UI thread, so a
long turn never blocks typing, and Ctrl-C can cancel it cleanly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Label,
    Markdown,
    OptionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from ..agent import ops, prefs
from ..agent.loop import (
    Agent,
    AgentConfig,
    Notice,
    TextDelta,
    ThinkingDelta,
    ToolFinished,
    ToolStarted,
    TurnComplete,
    Usage,
)
from ..agent.permissions import (
    ALLOW,
    ALLOW_ALWAYS,
    DENY,
    ApprovalRequest,
    PermissionManager,
    default_policies,
)
from ..agent.providers import get_provider
from ..agent.session import Session, SessionStore
from . import commands, logo

CSS = """
Screen { layout: vertical; }
#transcript { height: 1fr; padding: 0 1; scrollbar-size-vertical: 1; }
#composer { height: auto; max-height: 12; border-top: solid $primary; }
#input { height: auto; min-height: 3; max-height: 10; border: none; }
#menu {
    height: auto; max-height: 12; border: round $accent;
    background: $surface; scrollbar-size-vertical: 1;
}
#menu > .option-list--option-highlighted {
    background: $accent; color: $text; text-style: bold;
}
#running { height: 1; padding: 0 1; color: $accent; }
#status { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
.logo { color: $accent; margin: 1 0 1 0; }
.msg-user { color: $success; margin: 1 0 0 0; }
.msg-assistant { margin: 0 0 1 0; }
.msg-notice { color: $warning; }
.msg-thinking { color: $text-muted; text-style: italic; }
.tool-card { color: $accent; margin: 0 0 0 2; }
.tool-error { color: $error; margin: 0 0 0 2; }
ChoiceScreen { align: center middle; }
#chooser {
    width: 72; max-width: 90%; height: auto; max-height: 80%;
    padding: 1 2; border: thick $accent; background: $surface;
}
#chooser-title { padding-bottom: 1; }
#chooser-list { height: auto; max-height: 20; border: none; background: $surface; }
ApprovalScreen { align: center middle; }
#approval { width: 80%; max-width: 100; height: auto; padding: 1 2;
            border: thick $warning; background: $surface; }
#approval-detail { max-height: 14; overflow-y: auto; color: $text-muted; }
#approval-buttons { height: auto; padding-top: 1; }
"""


class ApprovalScreen(ModalScreen[str]):
    """Blocks a mutating tool call until the user decides."""

    BINDINGS = [
        Binding("y", "allow", "Allow"),
        Binding("a", "always", "Always"),
        Binding("n", "deny", "Deny"),
        Binding("escape", "deny", "Deny"),
    ]

    def __init__(self, request: ApprovalRequest, heading: str | None = None,
                 show_always: bool = True) -> None:
        super().__init__()
        self.request = request
        self.heading = heading or f"[b]{request.tool}[/b] wants to run"
        # A session-wide decision has no "always" — allow already means always.
        self.show_always = show_always

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="approval"):
            yield Label(self.heading, id="approval-title")
            yield Static(self.request.detail or self.request.summary, id="approval-detail")
            with Horizontal(id="approval-buttons"):
                yield Button("Allow (y)", variant="success", id="allow")
                if self.show_always:
                    label = (f"Always `{self.request.prefix}` (a)"
                             if self.request.prefix else "Always this tool (a)")
                    yield Button(label, variant="primary", id="always")
                yield Button("Deny (n)", variant="error", id="deny")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss({"allow": ALLOW, "always": ALLOW_ALWAYS}.get(event.button.id or "", DENY))

    def action_allow(self) -> None:
        self.dismiss(ALLOW)

    def action_always(self) -> None:
        self.dismiss(ALLOW_ALWAYS)

    def action_deny(self) -> None:
        self.dismiss(DENY)


class Submitted(Message):
    """User pressed Enter with content."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class ChoiceScreen(ModalScreen[str | None]):
    """An arrow-navigable picker for provider, model, mode, effort, sessions."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel", show=False),
    ]

    def __init__(self, title: str, choices: list[commands.Choice],
                 current: str | None = None) -> None:
        super().__init__()
        self.title_text = title
        self.choices = choices
        self.current = current

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chooser"):
            yield Label(f"[b]{self.title_text}[/b]", id="chooser-title")
            options = OptionList(id="chooser-list")
            yield options
            yield Label("[dim]↑↓ navigate · ⏎ select · Esc cancel[/dim]")

    def on_mount(self) -> None:
        options = self.query_one("#chooser-list", OptionList)
        width = max((len(c.label) for c in self.choices), default=0)
        selected = 0
        for index, choice in enumerate(self.choices):
            marks = " ●" if choice.id == self.current else ("  ⭐" if choice.recommended
                                                            else "")
            text = f"{choice.label.ljust(width)}{marks}"
            if choice.description:
                text += f"   [dim]{choice.description}[/dim]"
            options.add_option(Option(text, id=choice.id))
            if choice.id == self.current:
                selected = index
        # Start on whatever is active, so Enter is a no-op rather than a change.
        options.highlighted = selected
        options.focus()

    @on(OptionList.OptionSelected, "#chooser-list")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RunningIndicator(Static):
    """Live "sema is working" line shown while a turn runs.

    Carries the things you actually want mid-turn: how long it has been going,
    what it has spent, and how to stop it.
    """

    FRAME_SECONDS = 0.12

    def __init__(self) -> None:
        super().__init__("", id="running")
        self.display = False
        self._frame = 0
        self._started = 0.0
        self._timer = None
        self.detail = "working"
        self.tokens = 0
        # Kept as a plain attribute: Static has no readable rendered text, and
        # this is what tests and any future consumer read.
        self.label_text = ""

    def start(self, detail: str = "working") -> None:
        self.detail = detail
        self.tokens = 0
        self._frame = 0
        self._started = monotonic()
        self.display = True
        self._refresh_label()
        if self._timer is None:
            self._timer = self.set_interval(self.FRAME_SECONDS, self._tick)

    def update_detail(self, detail: str, tokens: int | None = None) -> None:
        self.detail = detail
        if tokens is not None:
            self.tokens = tokens
        if self.display:
            self._refresh_label()

    def stop(self) -> None:
        self.display = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(logo.SPINNER)
        self._refresh_label()

    def _refresh_label(self) -> None:
        # Not named _render: Widget._render() is part of Textual's rendering
        # pipeline, and overriding it blanks the widget.
        elapsed = int(monotonic() - self._started)
        parts = [f"{elapsed}s"]
        if self.tokens:
            parts.append(f"{self.tokens:,} tokens")
        parts.append("ctrl+c to interrupt")
        self.label_text = (
            f"{logo.SPINNER[self._frame]} sema · {self.detail} ({' · '.join(parts)})"
        )
        self.update(
            f"{logo.SPINNER[self._frame]} [b]sema[/b] · {self.detail} "
            f"[dim]({' · '.join(parts)})[/dim]"
        )


class CommandMenu(OptionList):
    """The `/` menu: filtered commands, navigated with the arrow keys.

    Rendered in the normal layout flow directly above the input rather than as
    a floating overlay, so it can never cover the transcript's last line.
    """

    def show_for(self, prefix: str) -> bool:
        """Populate from a partially-typed command. True if anything matched."""
        found = commands.matches(prefix)
        self.clear_options()
        if not found:
            self.display = False
            return False
        width = max(len(spec.name) for spec in found)
        for spec in found:
            self.add_option(
                Option(f"/{spec.name.ljust(width)}   {spec.summary}", id=spec.name)
            )
        self.display = True
        self.highlighted = 0
        return True

    def hide(self) -> None:
        self.display = False
        self.clear_options()

    @property
    def is_open(self) -> bool:
        return bool(self.display) and self.option_count > 0

    def selected_name(self) -> str | None:
        if not self.is_open or self.highlighted is None:
            return None
        return self.get_option_at_index(self.highlighted).id

    def move(self, delta: int) -> None:
        if not self.is_open:
            return
        self.highlighted = ((self.highlighted or 0) + delta) % self.option_count


class ChatInput(TextArea):
    """Input with history, Enter-to-send, and the `/` command menu."""

    BINDINGS = [
        Binding("enter", "submit", "Send", priority=True),
        Binding("tab", "complete", "Complete", priority=True, show=False),
        Binding("escape", "dismiss_menu", "Close menu", show=False),
        Binding("up", "history_prev", "Prev", show=False),
        Binding("down", "history_next", "Next", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # Named `sent_history`, not `history`: TextArea.history is its own undo
        # stack, and shadowing it breaks the widget.
        self.sent_history: list[str] = []
        self._cursor = 0

    @property
    def menu(self) -> "CommandMenu | None":
        try:
            return self.screen.query_one(CommandMenu)
        except Exception:  # noqa: BLE001 - absent when the widget is used alone
            return None

    def refresh_menu(self) -> None:
        """Open, filter, or close the menu to match what has been typed."""
        menu = self.menu
        if menu is None:
            return
        if commands.is_command_prefix(self.text):
            menu.show_for(self.text.strip())
        else:
            menu.hide()

    def accept_menu(self, submitting: bool = False) -> bool:
        """Complete the highlighted command. True if the menu handled the key."""
        menu = self.menu
        if menu is None or not menu.is_open:
            return False
        name = menu.selected_name()
        if submitting and self.text.strip() == f"/{name}":
            # The command is already typed in full — Enter should run it, not
            # re-complete what is already there.
            menu.hide()
            return False
        menu.hide()
        if not name:
            return False
        spec = commands.REGISTRY[name]
        # Commands that take arguments leave a trailing space and the caret
        # ready; the rest are complete as typed.
        takes_args = spec.usage.strip() != f"/{spec.name}"
        self.text = f"/{name} " if takes_args else f"/{name}"
        self.move_cursor(self.document.end)
        return True

    def action_complete(self) -> None:
        """Tab: accept the highlighted command, or open the menu."""
        if not self.accept_menu():
            self.refresh_menu()

    def action_dismiss_menu(self) -> None:
        menu = self.menu
        if menu is None or not menu.is_open:
            return
        menu.hide()
        if self.text.strip() == "/":
            # A lone slash was only ever an intent to browse commands. Clearing
            # it leaves an empty prompt ready to type into.
            self.text = ""

    def action_submit(self) -> None:
        # Enter completes the highlighted command rather than sending, which is
        # how every other completion popup behaves — unless the command is
        # already typed out, in which case it runs.
        if self.accept_menu(submitting=True):
            return
        text = self.text.strip()
        if not text:
            return
        self.sent_history.append(text)
        self._cursor = len(self.sent_history)
        self.text = ""
        if self.menu is not None:
            self.menu.hide()
        self.post_message(Submitted(text))

    def action_history_prev(self) -> None:
        menu = self.menu
        if menu is not None and menu.is_open:
            menu.move(-1)
            return
        # Only take over Up when the caret is on the first line, so multiline
        # editing still works.
        if self.cursor_location[0] != 0 or not self.sent_history:
            self.move_cursor_relative(rows=-1)
            return
        self._cursor = max(0, self._cursor - 1)
        self.text = self.sent_history[self._cursor]

    def action_history_next(self) -> None:
        menu = self.menu
        if menu is not None and menu.is_open:
            menu.move(1)
            return
        if not self.sent_history:
            return
        self._cursor = min(len(self.sent_history), self._cursor + 1)
        self.text = (
            "" if self._cursor >= len(self.sent_history)
            else self.sent_history[self._cursor]
        )


class SemaChatApp(App):
    """The sema terminal app."""

    CSS = CSS
    TITLE = "sema"
    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel / quit", priority=True),
        Binding("ctrl+t", "toggle_thinking", "Thinking"),
        Binding("shift+tab", "cycle_mode", "Mode"),
        Binding("ctrl+l", "clear", "Clear"),
    ]

    def __init__(
        self,
        root: Path,
        provider_id: str | None = None,
        model: str = "",
        mode: str | None = None,
        session_id: str | None = None,
        yes: bool = False,
        base_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.root = root
        self.base_dir = base_dir
        self.store = SessionStore(base_dir, str(root))
        # Saved preferences are the baseline; explicit flags override them.
        self.prefs = prefs.load(base_dir)
        self._provider_id = provider_id or self.prefs.provider
        chosen_mode = mode or self.prefs.mode
        provider = get_provider(self._provider_id)
        self._provider_id = provider.id  # normalize an unknown id to the default
        self.session = (
            self.store.load(session_id) if session_id else None
        ) or Session.create(
            self._provider_id,
            model or self.prefs.model or provider.default_model,
            chosen_mode,
        )
        if model:
            self.session.model = model
        if self.session.model not in {m.id for m in provider.models}:
            self.session.model = provider.default_model
        self.session.mode = chosen_mode
        if self.prefs.effort:
            self.session.effort = self.prefs.effort
        self.watcher = ops.Watcher(root)
        self.use_index = ops.has_index(root)
        self.pending_attachments: list = []
        self.permissions = PermissionManager(
            policies=default_policies(), asker=self._ask_permission, bypass=yes
        )
        self.show_thinking = False
        self._turn = None
        self._index_notice: str | None = None
        # Tri-state: None = not yet asked whether a CLI agent may edit files.
        self._cli_edit_consent: bool | None = True if yes else None

    # ── layout ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="transcript")
        menu = CommandMenu(id="menu")
        menu.display = False
        yield menu
        yield RunningIndicator()
        yield ChatInput(id="input", soft_wrap=True)
        yield Static("", id="status")
        yield Footer()

    @on(TextArea.Changed, "#input")
    def _on_input_changed(self, event: TextArea.Changed) -> None:
        # Typing `/` opens the menu; typing past the command name closes it.
        # Take the widget from the event — this also fires while the screen is
        # being torn down, when a query would raise NoMatches.
        widget = event.text_area
        if isinstance(widget, ChatInput):
            widget.refresh_menu()

    @on(OptionList.OptionSelected, "#menu")
    def _on_menu_click(self, event: OptionList.OptionSelected) -> None:
        field = self.query_one("#input", ChatInput)
        self.query_one(CommandMenu).highlighted = event.option_index
        field.accept_menu()
        field.focus()

    def on_mount(self) -> None:
        ops.silence_progress_bars()
        if self.use_index:
            self._index_notice = ops.bind_index(self.root)
        else:
            self._index_notice = f"No index at {self.root}. Run `/index` to build one."
        self.query_one("#input", ChatInput).focus()
        self._banner()
        self._refresh_status()

    def _banner(self) -> None:
        provider = get_provider(self._provider_id)
        # The wordmark is pre-rendered art, so it goes in a Static — Markdown
        # would reflow it and collapse the runs of block characters.
        self.logo_text = logo.render(self.size.width or 100)
        self._append_static(self.logo_text, "logo")
        lines = [
            f"`{self.root}` · **{provider.label}** · `{self.session.model}` "
            f"· mode **{self.session.mode}**",
        ]
        if self._index_notice:
            lines.append(f"> {self._index_notice}")
        lines.append(
            "Type a prompt, or press **/** for the command menu "
            "(↑↓ to navigate, ⏎ or Tab to pick, Esc to close)."
        )
        self._append("\n\n".join(lines), "msg-notice")
        if self.session.messages:
            self._append(
                f"_Resumed session `{self.session.id}` "
                f"({len(self.session.messages)} messages)._", "msg-notice"
            )

    # ── transcript ───────────────────────────────────────────────────────

    def _append(self, markdown: str, classes: str = "") -> Markdown:
        widget = Markdown(markdown, classes=classes)
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.mount(widget)
        transcript.scroll_end(animate=False)
        return widget

    def _append_static(self, text: str, classes: str = "") -> Static:
        widget = Static(text, classes=classes)
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.mount(widget)
        transcript.scroll_end(animate=False)
        return widget

    def clear_transcript(self) -> None:
        self.query_one("#transcript", VerticalScroll).remove_children()

    def _refresh_status(self) -> None:
        usage = self.session.usage
        cost = f"${usage.cost:.3f}" if usage.cost_known else "—"
        watch = " ●watch" if self.watcher.running else ""
        index = "" if self.use_index else " ⚠no-index"
        attached = f" 📎{len(self.pending_attachments)}" if self.pending_attachments else ""
        # Kept as a plain attribute too: Static has no readable `renderable`,
        # so this is what the tests and any future status consumers read.
        total = usage.input + usage.output
        # Most of a CLI agent's prompt is its own fixed context re-sent each
        # call. Showing the cached share stops a big number reading as a leak.
        cached = f" ({usage.cached:,} cached)" if usage.cached else ""
        self.status_text = (
            f"{self.session.mode} · {self._provider_id}/{self.session.model} "
            f"· {self.session.effort} · {total:,} tok{cached} · {cost}"
            f"{watch}{index}{attached}"
        )
        self.query_one("#status", Static).update(self.status_text)

    # ── AppContext protocol ──────────────────────────────────────────────

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def _remember(self) -> None:
        """Persist the current selection so the next run starts here."""
        self.prefs = prefs.Prefs(
            provider=self._provider_id,
            model=self.session.model,
            mode=self.session.mode,
            effort=self.session.effort,
        )
        prefs.save(self.prefs, self.base_dir)

    def set_provider(self, provider_id: str) -> None:
        self._provider_id = provider_id
        provider = get_provider(provider_id)
        self.session.provider = provider_id
        if self.session.model not in {m.id for m in provider.models}:
            self.session.model = provider.default_model
        # A new provider is a new consent question.
        if not self.permissions.bypass:
            self._cli_edit_consent = None
        self._remember()
        self._refresh_status()

    def set_model(self, model: str) -> None:
        self.session.model = model
        self._remember()
        self._refresh_status()

    def set_mode(self, mode: str) -> None:
        self.session.mode = mode
        self._remember()
        self._refresh_status()

    def set_effort(self, effort: str) -> None:
        self.session.effort = effort
        self._remember()
        self._refresh_status()

    def new_session(self) -> None:
        provider = get_provider(self._provider_id)
        self.session = Session.create(
            self._provider_id, self.session.model or provider.default_model,
            self.session.mode
        )
        self.pending_attachments = []
        self.clear_transcript()
        self._refresh_status()

    def load_session(self, session_id: str) -> bool:
        loaded = self.store.load(session_id)
        if loaded is None:
            return False
        self.session = loaded
        self._provider_id = loaded.provider
        self.clear_transcript()
        for message in loaded.messages:
            self._append(message.content,
                         "msg-user" if message.role == "user" else "msg-assistant")
        self._refresh_status()
        return True

    def request_quit(self) -> None:
        self.exit()

    # ── permissions ──────────────────────────────────────────────────────

    async def _ask_permission(self, request: ApprovalRequest) -> str:
        return await self.push_screen_wait(ApprovalScreen(request))

    async def choose(self, title: str, options: list[commands.Choice],
                     current: str | None = None) -> str | None:
        """Open a picker and wait for the user's choice (None if cancelled)."""
        if not options:
            return None
        return await self.push_screen_wait(ChoiceScreen(title, options, current))

    # ── turn handling ────────────────────────────────────────────────────

    @on(Submitted)
    def _on_submitted(self, event: Submitted) -> None:
        # In a worker because a slash command may open a picker, and
        # push_screen_wait is only legal from one.
        self.run_worker(self.handle_input(event.text), name="input")

    async def handle_input(self, text: str) -> str | None:
        """Route one line of input: slash command, or a model turn.

        Returns the command's reply (``None`` when the input started a turn) so
        this path is drivable from a test without a keyboard.
        """
        if self._turn is not None and self._turn.is_running:
            message = "A turn is already running — press Ctrl-C to cancel it."
            self._append(f"_{message}_", "msg-notice")
            return message
        reply = await commands.dispatch(self, text)
        if reply is not None:
            if reply:
                self._append(reply, "msg-notice")
            self._refresh_status()
            return reply
        self._append(text, "msg-user")
        self._turn = self.run_turn(text)
        return None

    async def _ensure_cli_edit_consent(self) -> None:
        """Ask once whether a CLI agent may edit files this session.

        These providers run their own tools under `-p` and cannot be prompted
        per call, so the choice is all-or-nothing and is made up front — rather
        than telling the user to restart with --yes on every single turn.
        """
        provider = get_provider(self._provider_id)
        if not (provider.reads_workspace and self.session.mode == "agent"):
            return
        if self.permissions.bypass or self._cli_edit_consent is not None:
            return
        decision = await self.push_screen_wait(ApprovalScreen(
            ApprovalRequest(
                tool=provider.label,
                summary="allow file edits and commands for this session",
                detail=(
                    f"{provider.label} runs its own tools and cannot ask about each "
                    "one, so this is a single decision for the whole session.\n\n"
                    f"Allow — {provider.label} may edit files and run commands in "
                    f"{self.root}.\n"
                    "Deny  — it answers read-only; any edit it attempts is refused.\n\n"
                    "You can switch to /mode plan or /mode ask for read-only work."
                ),
            ),
            heading=f"Let [b]{provider.label}[/b] edit files this session?",
            show_always=False,
        ))
        self._cli_edit_consent = decision in (ALLOW, ALLOW_ALWAYS)
        self.permissions.bypass = self._cli_edit_consent
        if self._cli_edit_consent:
            self._append(
                f"_{provider.label} may edit files for this session._", "msg-notice"
            )
        else:
            self._append(
                f"_{provider.label} is read-only this session; edits will be refused._",
                "msg-notice",
            )

    @work(exclusive=True)
    async def run_turn(self, text: str) -> None:
        await self._ensure_cli_edit_consent()
        config = AgentConfig(
            root=self.root,
            provider=get_provider(self._provider_id),
            model=self.session.model,
            mode=self.session.mode,
            effort=self.session.effort,
            permissions=self.permissions,
            use_index=self.use_index,
        )
        attachments_dir = self.store.attachments_dir(self.session.id)
        agent = Agent(config, self.session, attachments_dir)
        attachments = list(self.pending_attachments)
        self.pending_attachments = []

        indicator = self.query_one(RunningIndicator)
        indicator.start("thinking")
        answer = self._append("", "msg-assistant")
        buffer: list[str] = []
        thinking_widget = None
        thinking_buffer: list[str] = []
        try:
            async for event in agent.run_turn(text, attachments):
                if isinstance(event, TextDelta):
                    if not buffer:
                        indicator.update_detail("responding")
                    buffer.append(event.text)
                    answer.update("".join(buffer))
                    self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)
                elif isinstance(event, ThinkingDelta):
                    if not self.show_thinking:
                        continue
                    if thinking_widget is None:
                        thinking_widget = self._append_static("", "msg-thinking")
                    thinking_buffer.append(event.text)
                    thinking_widget.update("".join(thinking_buffer))
                elif isinstance(event, Notice):
                    self._append_static(event.text, "msg-notice")
                elif isinstance(event, ToolStarted):
                    indicator.update_detail(f"{event.name}")
                    self._append_static(f"⚒ {event.name}  {event.summary}", "tool-card")
                elif isinstance(event, ToolFinished):
                    preview = event.output.strip().splitlines()
                    head = preview[0][:120] if preview else "(no output)"
                    more = f"  (+{len(preview) - 1} lines)" if len(preview) > 1 else ""
                    style = "tool-error" if event.is_error else "tool-card"
                    self._append_static(f"  ↳ {head}{more}", style)
                elif isinstance(event, Usage):
                    usage = self.session.usage
                    indicator.update_detail(
                        indicator.detail,
                        usage.input + usage.output + event.input_tokens
                        + event.output_tokens,
                    )
                    self._refresh_status()
                elif isinstance(event, TurnComplete):
                    if event.plan_path:
                        self._append(f"_Plan saved to `{event.plan_path}`._", "msg-notice")
        except asyncio.CancelledError:
            self._append_static("⏹ cancelled", "msg-notice")
            raise
        except Exception as exc:  # noqa: BLE001 - never kill the app on a bad turn
            self._append(f"**Error:** {type(exc).__name__}: {exc}", "msg-notice")
        finally:
            indicator.stop()
            self.store.save(self.session)
            self._refresh_status()

    # ── actions ──────────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        if self._turn is not None and self._turn.is_running:
            self._turn.cancel()
            return
        self.exit()

    def action_toggle_thinking(self) -> None:
        self.show_thinking = not self.show_thinking
        self._append_static(
            f"Thinking pane {'on' if self.show_thinking else 'off'}.", "msg-notice"
        )

    def action_cycle_mode(self) -> None:
        order = commands.MODES
        index = (order.index(self.session.mode) + 1) % len(order)
        self.set_mode(order[index])

    def action_clear(self) -> None:
        self.clear_transcript()

    async def on_unmount(self) -> None:
        if self.watcher.running:
            await self.watcher.stop()
        self.store.save(self.session)
