from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import threading
import time
from functools import partial
from pathlib import Path
from typing import Callable, Iterable, List, NamedTuple, Optional, Tuple

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import Button, Input, RichLog, Static
from textual.worker import Worker

from arg_parse import HelpText, parse_tokens, setup_parsers, unknown_command_message
from completions import TunrSuggester
from dashboard import DashboardScreen
from main import PlaylistCLI, configure_logging, dispatch_command
from results_screen import ResultsAction, ResultsScreen, results_for_browse
from spotify_manager import (
    SPOTIFY_ENV_KEYS,
    get_cached_token_info,
    missing_scopes,
    scope_error_hint,
)
from ui import (
    ACCENT_BLUE,
    SUBSECTION_STYLE,
    clear_preview,
    error_panel,
    info,
    json_output,
    key_value_table,
    section,
    set_output_sink,
    set_preview_sink,
    set_status_sink,
    subsection,
    table,
    warning,
)
from web_search import detect_search_commands

logger = logging.getLogger(__name__)

# OP-1 (Teenage Engineering) theme: warm off-white ink on near-black chrome,
# with the OP-1 accents mapped onto Textual's semantic slots. The App CSS
# below uses theme variables ($background, $surface, …) so all chrome follows
# this palette; the Rich-side twin lives in ui.py (ACCENT_* / ink constants).
OP1_THEME = Theme(
    name="op-1",
    primary="#00b4e6",  # op-1 blue
    secondary="#8a8d8f",  # muted grey
    accent="#f26200",  # op-1 orange
    foreground="#fffff6",  # warm white
    background="#0d0d0d",
    surface="#1c1e1f",
    panel="#2a2c2e",
    success="#00e05a",  # op-1 green
    warning="#f2a900",
    error="#ff4f4f",
    dark=True,
)

# Persisted command history is capped to this many lines (enforced on load).
HISTORY_MAX_LINES = 500

# Canonical list lives in spotify_manager (shared with /status).
SPOTIFY_REQUIRED_KEYS = list(SPOTIFY_ENV_KEYS)

SEARCH_OPTIONAL_KEYS = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
]

COMMANDS_ALLOWED_WITHOUT_SPOTIFY = {
    # Read-only local snapshot — designed for the "is my setup even
    # configured?" audience, so it must run before credentials exist.
    "status",
    "backup",
    "list-backups",
    "restore",
    "list-rotations",
    "stats",
    "profile",
    "taste",
    "plan",
    "search",
    "find",
    "enrich",
    "sonic",
    # GDPR-export import touches only the local DB/filesystem — exactly the
    # audience without API credentials yet.
    "import-history",
    # Token-file deletion only; needs no API credentials (and is exactly what
    # a half-configured setup may need to get unstuck).
    "auth-reset",
    "interactive",
    # Offline embedding backfill + local KNN — local DB and model only.
    "embed",
    "similar",
}

# Task-based grouping for the /help listing. Commands not named here fall into
# an "Other" bucket; legacy commands are hidden unless `/help all` is used. The
# descriptions still come from argparse, so this map only controls ORDER/grouping.
HELP_GROUPS: "list[tuple[str, list[str]]]" = [
    (
        "Set up",
        [
            "status",
            "auth-status",
            "auth-refresh",
            "auth-reset",
            "pull",
            "ingest",
            "listen-sync",
            "import-history",
            "enrich",
            "sonic",
        ],
    ),
    (
        "Playlists",
        [
            "update",
            "rotate",
            "sync",
            "undo",
            "plan",
            "diff",
            "view",
            "extract",
            "clean",
            "backup",
            "restore",
            "restore-previous-rotation",
        ],
    ),
    ("Discover", ["find", "search", "results", "similar", "embed"]),
    ("Insight", ["dash", "stats", "profile", "taste", "list-rotations", "list-backups"]),
]
HELP_LEGACY = {"import"}

# One-line help for the TUI-only meta commands (mirrors the Session table in
# _show_help). Used by `/help <name>` and as did-you-mean candidates.
META_COMMAND_HELP = {
    "help": "Show the command list (/help all includes legacy commands)",
    "?": "Show the command list (/help all includes legacy commands)",
    "setup": "Show first-time setup instructions",
    "env": "Show detected environment keys",
    "keys": "Show detected environment keys",
    "debug": "Usage: /debug [errors|last|track <id>]",
    "errors": "Show error log (alias for /debug errors)",
    "expand": "Expand the last search",
    "search-more": "Expand the last search",
    "dash": "Open the interactive dashboard (taste · stats · plays)",
    "dashboard": "Open the interactive dashboard (taste · stats · plays)",
    "results": (
        "Browse the last /search or /find results "
        "(enter=find-similar, i=inspect, c=copy id, o=url, space=select, a/p=apply)"
    ),
    "browse": (
        "Browse the last /search or /find results "
        "(enter=find-similar, i=inspect, c=copy id, o=url, space=select, a/p=apply)"
    ),
    "clear": "Clear the output pane",
    "cls": "Clear the output pane",
    "quit": "Exit the app",
    "exit": "Exit the app",
}


class PaletteCommand(NamedTuple):
    """One tunr command as surfaced in the ctrl+p command palette."""

    name: str
    help: str
    needs_argument: bool


class TunrCommandProvider(Provider):
    """Command-palette provider for tunr slash commands (ctrl+p).

    Fuzzy-searches every advertised registry command plus the TUI meta
    commands, each with its one-line help. Selecting an arg-taking command
    preloads ``/cmd `` into the input for the user to finish; a provably
    no-arg command is submitted through the exact same path (and gates) as
    typed input.
    """

    def _palette_app(self) -> "Optional[PlaylistInteractiveApp]":
        """The tunr app, or None when the palette should offer nothing.

        Offers nothing when the palette was opened over a pushed screen
        (e.g. /dash): the command input is not reachable there, and running
        a command would race the modal screen's synchronous reads of the
        shared sqlite connection.
        """
        app = self.app
        if not isinstance(app, PlaylistInteractiveApp):
            return None
        stack = app.screen_stack
        if stack and self.screen is not stack[0]:
            return None
        return app

    @staticmethod
    def _callback(app: "PlaylistInteractiveApp", entry: PaletteCommand) -> Callable[[], None]:
        """Pick the palette action for one command (insert vs submit)."""
        if entry.needs_argument:
            return partial(app._palette_insert, entry.name)
        return partial(app._palette_submit, entry.name)

    async def search(self, query: str) -> Hits:
        app = self._palette_app()
        if app is None:
            return
        matcher = self.matcher(query)
        for entry in app._palette_commands():
            display = f"/{entry.name}"
            score = matcher.match(display)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(display),
                    self._callback(app, entry),
                    text=display,
                    help=entry.help,
                )

    async def discover(self) -> Hits:
        app = self._palette_app()
        if app is None:
            return
        for entry in app._palette_commands():
            display = f"/{entry.name}"
            yield DiscoveryHit(display, self._callback(app, entry), help=entry.help)


class UILogHandler(logging.Handler):
    def __init__(self, app: "PlaylistInteractiveApp") -> None:
        super().__init__()
        self.app = app
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            style = "white"
            if record.levelno >= logging.ERROR:
                style = "red"
                # Error-aware completion: count ERROR+ records (never WARNING)
                # against the currently running command. Thread-safety note:
                # emit() runs on whichever thread logged (usually the command
                # worker); the reset happens on the app thread in _run_command
                # BEFORE the worker starts, and the read happens on the worker
                # thread in _execute_command AFTER dispatch returns. The
                # one-command-at-a-time `status` gate means those never
                # overlap, and the int increment itself is GIL-atomic — no
                # lock needed. Esc-to-cancel addendum: a cancelled run's
                # thread may log ERRORs after a NEW command started; its
                # thread-bound generation is stale, so the increment is
                # skipped and the new command's error window stays clean
                # (its scrollback line is dropped by the same generation
                # guard inside _dispatch_ui).
                if not self.app._calling_thread_is_stale():
                    self.app._command_error_count += 1
            elif record.levelno >= logging.WARNING:
                style = "yellow"
            elif record.levelno >= logging.INFO:
                style = ACCENT_BLUE
            elif record.levelno >= logging.DEBUG:
                style = "dim"
            text = Text(message, style=style)
            self.app._dispatch_ui(self.app.append_log, text)
            if record.levelno >= logging.WARNING:
                self.app._dispatch_ui(self.app.record_error, message)
        except Exception:
            self.handleError(record)


class ConfirmScreen(ModalScreen[bool]):
    """Generic yes/no modal gating destructive commands (TUI path only).

    y (or the yes button) confirms; n / esc (or the no button) cancels.
    Deliberately NO screen-level Enter binding, and "no" is focused on
    mount: Enter is the very key that just submitted the command, so a
    queued second Enter (terminal key repeat, a reflexive double-tap — the
    exact accident class this gate exists to stop) must land on the SAFE
    button, never confirm the destructive action. Confirming requires a
    distinct, deliberate keypress: y, or tab to the yes button + enter.
    The screen is pushed, so its own Escape binding is found before
    the app-level Esc-cancel binding — Esc here closes only the modal
    (pinned by the confirm-modal pilot test). Styling rides the OP-1 theme
    variables the App CSS already resolves ($surface/$panel/$warning/…).
    """

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen #confirm_box {
        width: 64;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $panel;
    }
    ConfirmScreen #confirm_title {
        color: $warning;
        text-style: bold;
    }
    ConfirmScreen #confirm_message {
        margin-top: 1;
        color: $foreground;
    }
    ConfirmScreen #confirm_buttons {
        margin-top: 1;
        height: auto;
        align-horizontal: center;
    }
    ConfirmScreen Button {
        margin: 0 2;
        min-width: 8;
        border: none;
        background: $panel;
        color: $foreground;
    }
    ConfirmScreen Button:focus {
        background: $primary;
        color: $background;
        text-style: bold;
    }
    ConfirmScreen #confirm_hint {
        margin-top: 1;
        color: $secondary;
    }
    """

    # No "enter" binding on purpose — see the class docstring: Enter must
    # only ever press the FOCUSED button (safe default: "no").
    BINDINGS = [
        Binding("y", "confirm", "yes", show=False),
        Binding("n", "cancel", "no", show=False),
        Binding("escape", "cancel", "no", show=False),
    ]

    def __init__(self, title: str, question: str) -> None:
        super().__init__()
        self._title = title
        self._question = question

    def compose(self) -> ComposeResult:
        with Container(id="confirm_box"):
            yield Static(self._title, id="confirm_title")
            yield Static(self._question, id="confirm_message")
            with Horizontal(id="confirm_buttons"):
                yield Button("yes", id="confirm_yes")
                yield Button("no", id="confirm_no")
            yield Static("y confirm · n/esc cancel · enter = focused button", id="confirm_hint")

    def on_mount(self) -> None:
        # Focus the SAFE button: the second Enter of an accidental double-tap
        # (already queued behind the one that submitted the command) presses
        # the focused widget, so it must cancel, never confirm — see the
        # class docstring.
        self.query_one("#confirm_no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm_yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class PlaylistInteractiveApp(App):
    CSS = """
    Screen {
        background: $background;
        color: $foreground;
    }
    #top_bar {
        height: 1;
        padding: 0 2;
        background: $surface;
        color: $secondary;
    }
    #body {
        height: 1fr;
        width: 1fr;
    }
    #output {
        padding: 1 2;
        width: 1fr;
        height: 1fr;
    }
    #search_preview {
        padding: 1 2;
        width: 1fr;
        display: none;
    }
    #setup_screen {
        padding: 2 4;
        width: 1fr;
        height: 1fr;
    }
    #command_input {
        dock: bottom;
        background: $surface;
        border: solid $panel;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear output"),
        # Esc cancels the running command (a no-op when idle). The app
        # composes no Footer, so show=False just records intent. Pushed
        # screens are unaffected: the command palette, /dash and /results all
        # bind Escape on their own screen, and the active screen's bindings
        # are found before this app-level one (pinned by the Esc pilot test).
        Binding("escape", "cancel_command", "Cancel command", show=False),
    ]

    # ctrl+p command palette: the stock providers plus the tunr command
    # inventory (see TunrCommandProvider above).
    COMMANDS = App.COMMANDS | {TunrCommandProvider}

    # Status sentinels are lowercase on purpose — the top bar renders them
    # verbatim, and the chrome follows TE's lowercase convention.
    status = reactive("idle")
    SPINNER_FRAMES = ["|", "/", "-", "\\"]

    # Statuses that count as "at rest" for the user-work gate. Status alone
    # is NOT sufficient: _is_idle additionally requires zero in-flight
    # user-work threads. After an Esc-cancel the status reads "cancelled"
    # while the old thread may still be unwinding (thread workers cannot be
    # force-stopped) — its generation is stale so it cannot touch the UI,
    # but letting NEW work start would overlap it on the shared
    # serialized-use sqlite connection (storage/db.py forbids overlapping
    # queries across threads, and repos-owned transactions would interleave)
    # and potentially on the same live Spotify playlist. The gate therefore
    # stays closed until _worker_thread_exited restores true idle;
    # background auto-sync waits for strict "idle" the same way
    # (see _maybe_auto_sync).
    IDLE_STATUSES = frozenset({"idle", "cancelled"})

    def __init__(self, cli: PlaylistCLI, parser: argparse.ArgumentParser) -> None:
        super().__init__()
        self.cli = cli
        self.parser = parser
        self._history_path = self._resolve_history_path()
        self._history: List[str] = self._load_history()
        self._suggester = self._build_suggester()
        self._history_index: Optional[int] = None
        self._history_prefix: str = ""
        self._navigating = False
        self._nav_placed_value: Optional[str] = None
        self._pending_action: Optional[str] = None
        self._pending_payload: dict = {}
        self._missing_spotify_keys: List[str] = []
        self._env_status: dict = {}
        self._setup_mode = False
        self._mounted = False
        self._error_log: List[str] = []
        self._spinner_index = 0
        self._spinner_timer = None
        self._run_started: Optional[float] = None
        self._last_run_note: str = ""
        # ERROR-level log records seen since the current command started
        # (incremented by UILogHandler.emit — see the thread-safety note there).
        self._command_error_count = 0
        # Latest pipeline stage string ("extract 87/120", "providers 3/10")
        # rendered live in the top bar while a command runs.
        self._stage: str = ""
        self._app_thread_id: Optional[int] = None
        # Esc-to-cancel plumbing. `_run_generation` is bumped on the app
        # thread at every user-work start AND at every cancel; each worker
        # thread captures the value at start (thread-local `_worker_gen`) and
        # every UI callback it marshals is re-checked on the app-thread side
        # (_run_if_current) — a stale (cancelled) worker can therefore never
        # write late output into the scrollback or flip status.
        self._run_generation = 0
        self._worker_gen = threading.local()
        # The single in-flight user-work worker (commands, /expand, applying
        # search results) — the target of Esc. Auto-sync never sets it.
        self._active_worker: Optional[Worker] = None
        # User-work threads still unwinding (a cancelled one keeps running
        # until its current step completes). True idle is restored only when
        # this reaches zero — see _worker_thread_exited.
        self._inflight_workers = 0
        # Single-slot command queue: ONE command submitted while work is in
        # flight waits here and starts only at TRUE idle (_maybe_dequeue).
        # A second submission while the slot is full is refused outright.
        self._queued_command: "Optional[Tuple[str, object]]" = None
        # Background listen-sync: warn once, then stay quiet on repeat failures.
        self._auto_sync_warned = False
        # Log the "no cached token" skip once per session, not every interval.
        self._auto_sync_token_warned = False
        # Set after an insufficient-scope (403) failure: retrying with the same
        # stale token can never succeed and burns the busy slot each interval,
        # so auto-sync stands down until a cached token granting all required
        # scopes appears (e.g. after /auth-reset --yes + re-auth).
        self._auto_sync_scope_blocked = False

    def compose(self) -> ComposeResult:
        yield Static(id="top_bar")
        with Container(id="body"):
            yield Static(id="search_preview")
            yield RichLog(id="output", highlight=False, markup=False, wrap=True, min_width=20)
            yield Static(id="setup_screen")
        yield Input(
            placeholder="type /help for commands",
            id="command_input",
            suggester=self._suggester,
        )

    def on_mount(self) -> None:
        # Theme plumbing first: register the OP-1 palette and switch to it so
        # the CSS theme variables above resolve against it (Textual 8.x's
        # documented flow: register_theme + set `theme` in on_mount).
        self.register_theme(OP1_THEME)
        self.theme = "op-1"
        self._app_thread_id = threading.get_ident()
        self._mounted = True
        set_output_sink(self._emit_renderable)
        set_preview_sink(self._emit_preview)
        set_status_sink(self._emit_status)
        configure_logging(handler=UILogHandler(self))
        self._refresh_env_status()
        self._update_top_bar()
        self._show_welcome()
        if self._missing_spotify_keys:
            self._show_setup()
        else:
            self._schedule_auto_sync()
        self.query_one(Input).focus()
        self._update_top_bar()

    def on_shutdown(self) -> None:
        self._mounted = False
        set_output_sink(None)
        set_preview_sink(None)
        set_status_sink(None)

    def on_resize(self) -> None:
        self._update_top_bar()
        self.refresh(layout=True)

    def _update_setup_screen(self) -> None:
        if not self._mounted:
            return
        output = self.query_one(RichLog)
        preview = self.query_one("#search_preview", Static)
        setup_screen = self.query_one("#setup_screen", Static)
        if self._setup_mode:
            output.display = False
            preview.display = False
            setup_screen.display = True
            setup_screen.update(self._render_setup_content())
        else:
            setup_screen.display = False
            output.display = True
        self._update_input_placeholder()

    def _emit_preview(self, renderable) -> None:
        """ui.set_preview_sink handler: marshal to the app thread, guarded.

        Routed through _dispatch_ui like the output sink, so a preview
        emitted by a worker thread lands on the app thread — and a stale
        (cancelled) worker's late preview is dropped instead of resurrecting
        the pane the cancel action just cleared.
        """
        self._dispatch_ui(self._apply_preview, renderable)

    def _apply_preview(self, renderable) -> None:
        if not self._mounted:
            return
        preview = self.query_one("#search_preview", Static)
        if renderable is None:
            preview.display = False
            preview.update("")
            return
        preview.display = True
        preview.update(renderable)

    def watch_status(self, value: str) -> None:
        # Busy means "any non-idle work", not just statuses that happen to
        # start with "running" (e.g. "applying search results"). "cancelled"
        # is a rest state: nothing the user is waiting on, so no spinner.
        busy = value not in {"idle", "setup required", "cancelled"} and not self._setup_mode
        if busy:
            self._start_spinner()
        else:
            self._stop_spinner()
        self._update_top_bar()

    def on_key(self, event) -> None:
        command_input = self.query_one(Input)
        if not command_input.has_focus:
            return
        if event.key == "up":
            self._history_prev()
            event.stop()
        elif event.key == "down":
            self._history_next()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        event.input.value = ""
        if not raw:
            return
        if self._pending_action and not raw.startswith("/"):
            self.append_log(Text(f"> {raw}", style="bold"))
            self._handle_pending_input(raw)
            return
        self._submit_text(raw)

    def _submit_text(self, raw: str) -> None:
        """Submit command text exactly as if it had been typed and entered.

        Single choke point shared by the input widget and the command
        palette: the setup-mode gate in _handle_command and the idle gate in
        _run_command both apply downstream, unchanged.
        """
        raw = raw.strip()
        if not raw:
            return
        if self._pending_action and raw.startswith("/"):
            # A slash command cancels the armed wizard so stray text typed
            # later is never silently consumed as a wizard answer.
            self._clear_pending()
            self.append_log(Text("Search follow-up dismissed.", style="dim"))
        self._append_history(raw)
        self._history_index = None
        # Submitting ends any history-navigation session: without this reset,
        # a stale _nav_placed_value would later misclassify identical typed
        # text as nav-placed and skip the prefix filter.
        self._navigating = False
        self._nav_placed_value = None
        self._history_prefix = ""
        self.append_log(Text(f"> {raw}", style="bold"))
        self._handle_command(raw)

    def action_clear_log(self) -> None:
        self.query_one(RichLog).clear()

    def action_quit(self) -> None:
        self.exit()

    def append_log(self, renderable) -> None:
        log = self.query_one(RichLog)
        log.write(renderable)

    def record_error(self, message: str) -> None:
        self._error_log.append(message)
        if len(self._error_log) > 200:
            self._error_log = self._error_log[-200:]

    def _dispatch_ui(self, fn: Callable, *args: object) -> None:
        """Run a UI mutation on the app thread regardless of the caller's thread.

        ``call_from_thread`` raises RuntimeError when invoked *from* the app
        thread (Textual 8.x), so UI-thread callers (e.g. /debug handlers that
        emit through the ui sinks) must call directly instead.

        Stale-worker guard: a call originating from a user-work thread
        carries the run generation that thread captured at start
        (_run_user_work binds it thread-locally); _run_if_current re-checks
        it on the app-thread side, so everything a cancelled run emits after
        Esc — scrollback lines, log records, stage/preview updates, its own
        _post_command — is dropped instead of rendered late. Callers with no
        bound generation (the app thread, auto-sync) are never guarded.
        """
        gen = getattr(self._worker_gen, "gen", None)
        if gen is None:
            self._dispatch_ui_unguarded(fn, *args)
        else:
            self._dispatch_ui_unguarded(self._run_if_current, gen, fn, *args)

    def _dispatch_ui_unguarded(self, fn: Callable, *args: object) -> None:
        """The raw thread-marshalling half of _dispatch_ui (no staleness check)."""
        if self._app_thread_id is None or threading.get_ident() == self._app_thread_id:
            fn(*args)
        else:
            self.call_from_thread(fn, *args)

    def _run_if_current(self, gen: int, fn: Callable, *args: object) -> None:
        """App-thread side of the stale-worker guard.

        Runs `fn` only when the emitting worker's generation is still the
        current one. Generation bumps happen exclusively on the app thread
        (start + cancel), and this check runs on the app thread too, so
        there is no window in which a cancelled worker's callback can slip
        through after action_cancel_command has returned.
        """
        if gen != self._run_generation:
            return
        fn(*args)

    def _calling_thread_is_stale(self) -> bool:
        """True when the calling thread belongs to a cancelled (stale) run.

        Worker-thread-side convenience for non-UI effects (toasts, the error
        counter). The read of `_run_generation` is GIL-atomic; the tiny race
        against a concurrent cancel is harmless for these best-effort
        consumers — everything UI-visible goes through _run_if_current, which
        is race-free.
        """
        gen = getattr(self._worker_gen, "gen", None)
        return gen is not None and gen != self._run_generation

    def _emit_renderable(self, renderable) -> None:
        self._dispatch_ui(self.append_log, renderable)

    def _emit_status(self, stage: Optional[str]) -> None:
        """ui.set_status_sink handler: stage strings from the pipeline worker."""
        self._dispatch_ui(self._set_stage, stage)

    def _set_stage(self, stage: Optional[str]) -> None:
        self._stage = stage or ""
        # The 0.2s spinner tick re-renders the bar anyway; this direct update
        # just makes the new stage visible immediately (and in tests).
        self._update_top_bar()

    @staticmethod
    def _resolve_history_path() -> Path:
        env_path = os.getenv("TUNR_HISTORY_PATH")
        if env_path:
            return Path(env_path).expanduser()
        project_root = Path(__file__).resolve().parent.parent
        return project_root / "data" / ".tunr_history"

    def _load_history(self) -> List[str]:
        """Best-effort load of persisted history; missing/corrupt files -> []."""
        try:
            raw_lines = self._history_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError, ValueError):
            return []
        lines = [line for line in raw_lines if line.strip()]
        if len(lines) > HISTORY_MAX_LINES:
            lines = lines[-HISTORY_MAX_LINES:]
            try:
                self._history_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except OSError:
                logger.debug("Could not truncate command history file", exc_info=True)
        return lines

    def _append_history(self, raw: str) -> None:
        """Record a submitted command, skipping consecutive duplicates."""
        # Exiting isn't worth recalling: persisting /quit makes the first
        # up-arrow of the NEXT session recall it, which invites a misfire.
        if raw.lstrip("/").strip().lower() in {"quit", "exit"}:
            return
        if self._history and self._history[-1] == raw:
            return
        self._history.append(raw)
        if len(self._history) > HISTORY_MAX_LINES:
            self._history = self._history[-HISTORY_MAX_LINES:]
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            with self._history_path.open("a", encoding="utf-8") as handle:
                handle.write(raw + "\n")
        except OSError:
            logger.debug("Could not persist command history", exc_info=True)

    def _matching_history_index(self, start: int, step: int) -> Optional[int]:
        """First history index from `start` (walking by `step`) matching the prefix."""
        prefix = self._history_prefix
        index = start
        while 0 <= index < len(self._history):
            if not prefix or self._history[index].startswith(prefix):
                return index
            index += step
        return None

    def _history_prev(self) -> None:
        if not self._history:
            return
        if self._history_index is None:
            # Starting navigation: typed text becomes a prefix filter. A value
            # the navigation itself placed in the input is not a typed prefix.
            current = self._get_input_value()
            if self._navigating and current == self._nav_placed_value:
                self._history_prefix = ""
            else:
                self._history_prefix = current
            match = self._matching_history_index(len(self._history) - 1, -1)
            if match is None:
                return
            self._history_index = match
        else:
            match = self._matching_history_index(self._history_index - 1, -1)
            if match is not None:
                self._history_index = match
            # else: stay on the oldest matching entry (same as before).
        self._set_input_value(self._history[self._history_index])

    def _history_next(self) -> None:
        if self._history_index is None:
            return
        match = self._matching_history_index(self._history_index + 1, 1)
        if match is not None:
            self._history_index = match
            value = self._history[match]
        else:
            self._history_index = None
            self._history_prefix = ""
            value = ""
        self._set_input_value(value)

    def _set_input_value(self, value: str) -> None:
        self._navigating = True
        self._nav_placed_value = value
        self._write_input(value)

    def _get_input_value(self) -> str:
        """Read the input widget's value (thin Textual seam; tests override)."""
        return self.query_one(Input).value

    def _write_input(self, value: str) -> None:
        """Write the input widget's value (thin Textual seam; tests override)."""
        command_input = self.query_one(Input)
        command_input.value = value
        if hasattr(command_input, "cursor_position"):
            command_input.cursor_position = len(value)

    def _handle_command(self, raw: str) -> None:
        text = raw.strip()
        if text.startswith("/"):
            text = text[1:].strip()
        if not text:
            return
        self._refresh_env_status()

        if text in ("help", "?") or text in ("help all", "help --all"):
            self._show_help(show_all=text in ("help all", "help --all"))
            return
        if text.startswith("help "):
            self._show_command_help(text[len("help ") :])
            return
        if text in ("setup",):
            self._show_setup()
            return
        if text in ("env", "keys"):
            self._show_env()
            return
        if text == "debug":
            self._show_debug_errors()
            return
        if text.startswith("debug "):
            self._handle_debug(text)
            return
        if text in ("errors",):
            self._show_debug_errors()
            return
        if text in ("expand", "search-more"):
            self._expand_search()
            return
        if text in ("dash", "dashboard"):
            self._open_dashboard()
            return
        if text in ("results", "browse"):
            self._open_results()
            return
        if text in ("clear", "cls"):
            self.action_clear_log()
            return
        if text in ("quit", "exit"):
            self.action_quit()
            return

        try:
            tokens = shlex.split(text)
        except ValueError as exc:
            self.append_log(error_panel(f"Invalid command syntax: {exc}"))
            return
        command, args, error = parse_tokens(tokens, extra_commands=self._meta_command_names())
        if error:
            if isinstance(error, HelpText):
                self.append_log(Panel(Text(str(error)), title="Help", border_style=ACCENT_BLUE))
            else:
                self.append_log(error_panel(error))
            return
        if command == "interactive":
            self.append_log(Text("Already in interactive mode.", style="yellow"))
            return
        if self._setup_mode and command not in COMMANDS_ALLOWED_WITHOUT_SPOTIFY:
            missing = ", ".join(self._missing_spotify_keys)
            self.append_log(
                error_panel(
                    f"Spotify keys missing: {missing}\nRun /setup for instructions.",
                    title="Setup Required",
                )
            )
            return
        if command in ("search", "find"):
            self._warn_if_search_providers_missing()
        self._run_command(command, args)

    def _warn_if_search_providers_missing(self) -> None:
        """Advisory pre-flight for /search and /find: when no deep-search
        provider is detected, say so up front — but still dispatch.

        Warn-don't-block, because the pipeline can serve a previously-cached
        query with zero providers (SearchPipeline.run consults its SQLite
        cache BEFORE run_providers), and a provider-less fresh run already
        fails fast inside the pipeline with ProviderConfigError off the
        identical detection (web_search.detect_search_commands — /status and
        /env report from it too). Never refusing keeps the TUI's outcomes
        identical to the headless path (main.dispatch_command has no gate).

        A cache-aware hard block was considered and rejected: probing the
        cache here would read the shared SQLite connection from the UI thread
        while a worker may hold it, violating the serialized-use contract
        documented in storage/db.py.
        """
        if detect_search_commands():
            return
        self.append_log(
            Panel(
                Text(
                    "No deep-search provider detected — a fresh /search or /find "
                    "will fail; only a previously-cached query can be served.\n"
                    "Set ANTHROPIC_API_KEY and/or OPENAI_API_KEY (or a "
                    "WEB_SEARCH_CLAUDE_CMD / WEB_SEARCH_CODEX_CMD / WEB_SEARCH_CMD "
                    "override) in config/.env, then restart tunr. /env shows what "
                    "is detected.",
                    style="yellow",
                ),
                title="No search provider detected",
                border_style="yellow",
            )
        )

    def _run_command(self, command: str, args: object) -> None:
        if not self._is_idle():
            # Work in flight (a running/unwinding user worker or auto-sync):
            # hold the submission in the one-deep queue instead of refusing.
            # Any OTHER non-idle state (e.g. the "setup required" rest state)
            # keeps the flat refusal — a queue there would never drain.
            if (
                self._inflight_workers > 0
                or self._active_worker is not None
                or self.status == "auto-sync"
            ):
                self._enqueue_command(command, args)
            else:
                self._refuse_if_busy()
            return
        question = self._destructive_question(command, args)
        if question is None:
            self._start_command(command, args)
            return

        # Destructive dispatch: interpose the confirm modal. TUI-only seam —
        # the headless path (main.dispatch_command) never routes through here
        # and keeps its behavior exactly as before.
        def _decide(confirmed: "Optional[bool]" = None) -> None:
            self._focus_input()
            if not confirmed:
                self.append_log(Text(f"/{command} cancelled — nothing changed.", style="dim"))
                return
            if command == "auth-reset":
                # The modal IS the confirmation here: a confirmed TUI run
                # acts instead of re-printing the "--yes to confirm" hint.
                # (Headless auth-reset still requires --yes, unchanged.)
                setattr(args, "yes", True)
            self._start_command(command, args)

        self.push_screen(ConfirmScreen(f"confirm /{command}", question), callback=_decide)

    def _destructive_question(self, command: str, args: object) -> "Optional[str]":
        """The confirm-modal question for a destructive dispatch, else None.

        Gated (judged by what each handler does):
        * restore — replaces the entire live data/ directory with a backup;
        * auth-reset — deletes the cached Spotify token file;
        * update (without --dry-run) — rewrites the real Spotify playlist;
        * rotate / rotate-played — modify the real playlist (no dry-run mode);
        * sync — adds AND removes real-playlist tracks to mirror the db;
        * clean (without --dry-run) — permanently deletes rows from the db.

        Deliberately NOT gated: undo and restore-previous-rotation (recovery
        commands whose whole purpose is reverting a bad write — friction
        there works against the user), backup/plan/diff and every read-only
        command.
        """
        playlist = getattr(args, "playlist", "")
        if command == "restore":
            name = getattr(args, "backup_name", "")
            return (
                f"Replace the live data/ directory with backup '{name}'? "
                "Current data is overwritten (current state is moved aside only "
                "during the swap)."
            )
        if command == "auth-reset":
            return (
                "Delete the cached Spotify token? The next Spotify command "
                "will re-open the consent screen."
            )
        if command == "update" and not getattr(args, "dry_run", False):
            return (
                f"Apply a fresh selection to Spotify playlist '{playlist}'? "
                "This rewrites the live playlist (use --dry-run to preview)."
            )
        if command in ("rotate", "rotate-played"):
            return (
                f"Rotate played tracks out of Spotify playlist '{playlist}'? "
                "This modifies the live playlist."
            )
        if command == "sync":
            return (
                f"Sync the whole database into Spotify playlist '{playlist}'? "
                "Tracks are added AND removed so the playlist mirrors the db."
            )
        if command == "clean" and not getattr(args, "dry_run", False):
            return (
                "Permanently remove dead or over-popular songs from the local "
                "database? (/clean --dry-run previews the removals.)"
            )
        return None

    def _start_command(self, command: str, args: object) -> None:
        """Actually launch the (possibly just-confirmed) command worker."""
        # Belt-and-braces: the modal round-trip could in principle race a
        # freshly armed auto-sync; re-check rather than overlap workers.
        if self._refuse_if_busy():
            return
        # Fresh error window + stage for this command. App-thread writes;
        # the in-flight gate above guarantees no earlier user-work thread is
        # still running (UILogHandler.emit drops stale increments as a second
        # line of defence), so the new window starts clean.
        self._command_error_count = 0
        self._stage = ""
        self._run_started = time.monotonic()
        self._start_user_worker(f"running /{command}", lambda: self._execute_command(command, args))

    def _is_idle(self) -> bool:
        """True when new user work may start: a rest status AND no user-work
        thread still unwinding (see IDLE_STATUSES and _refuse_if_busy)."""
        return self.status in self.IDLE_STATUSES and self._inflight_workers == 0

    def _refuse_if_busy(self) -> bool:
        """Gate for new user work: when blocked, log why and return True.

        Two refusal flavours: a live command/auto-sync holds the gate, or an
        Esc-cancelled worker's thread is still unwinding — running anything
        new alongside it would interleave transactions on the shared
        serialized-use sqlite connection (and could overlap live Spotify
        mutations), so the gate reopens only at true idle
        (_worker_thread_exited). The generation guard only suppresses the
        old run's OUTPUT; this gate is what serializes its EFFECTS.
        """
        if self._is_idle():
            return False
        if self.status == "cancelled":
            self.append_log(
                Text(
                    "Waiting for the cancelled command to finish unwinding — "
                    "try again in a moment.",
                    style="yellow",
                )
            )
        elif self.status in self.IDLE_STATUSES:
            # Microscopic window: _post_command already flipped the status to
            # "idle" from the worker thread, but that thread's exit
            # notification hasn't landed yet (_worker_thread_exited). Refuse
            # honestly rather than assume the thread is past its shared work.
            self.append_log(
                Text("The previous command is still finishing — try again.", style="yellow")
            )
        else:
            self.append_log(Text("Another command is already running.", style="yellow"))
        return True

    def _enqueue_command(self, command: str, args: object) -> None:
        """Hold ONE submitted command until true idle (the single-slot queue).

        The slot is advisory state only: nothing here starts work, so every
        serialization invariant (_is_idle, the unwind gate, the generation
        guard) is untouched. Destructive commands are deliberately NOT
        confirmed here — their ConfirmScreen fires at dequeue time
        (_maybe_dequeue -> _run_command), when they are actually about to
        start, so the user confirms against the real moment of execution.
        A second submission while the slot is full is refused in the same
        honest style as the old always-refuse gate.
        """
        if self._queued_command is not None:
            queued_name = self._queued_command[0]
            self.append_log(
                Text(
                    f"Another command is already running and /{queued_name} is queued — "
                    "the queue holds one command. Wait, or press Esc to cancel both.",
                    style="yellow",
                )
            )
            return
        self._queued_command = (command, args)
        self._update_top_bar()
        self.append_log(
            Text(
                f"Queued /{command} — it starts when the current work finishes. "
                "Esc cancels the running command and drops the queue.",
                style="yellow",
            )
        )

    def _maybe_dequeue(self) -> None:
        """Start the queued command iff TRUE idle has been restored.

        Called from the two places idleness is genuinely restored: the
        user-work thread's exit notification (_worker_thread_exited) and
        auto-sync teardown (_finish_auto_sync). The _is_idle() check is the
        SAME gate _run_command enforces, so while an Esc-cancelled worker is
        still unwinding (status "cancelled", in-flight > 0) nothing dequeues
        — the queued command would otherwise overlap the stale thread on the
        shared serialized-use sqlite connection. Dequeueing re-enters
        _run_command, so a destructive queued command gets its ConfirmScreen
        now — at start time, not at enqueue time.
        """
        if self._queued_command is None or not self._is_idle():
            return
        command, args = self._queued_command
        self._queued_command = None
        self._update_top_bar()
        self.append_log(Text(f"Starting queued /{command}.", style="dim"))
        self._run_command(command, args)

    def _start_user_worker(self, status: str, work: Callable[[], None]) -> None:
        """Start THE user-work thread worker (single, Esc-cancellable).

        All three launch sites (_run_command, _expand_search,
        _apply_search_results) funnel through here so every user-visible run
        gets the same cancellation contract: a fresh generation, the
        in-flight counter, and the _active_worker handle Esc targets.
        """
        self._run_generation += 1
        gen = self._run_generation
        self._inflight_workers += 1
        self.status = status
        self._active_worker = self.run_worker(lambda: self._run_user_work(gen, work), thread=True)

    def _run_user_work(self, gen: int, work: Callable[[], None]) -> None:
        """Thread-side wrapper for every user-work worker.

        Binds the run generation to this thread (read back by _dispatch_ui
        and _calling_thread_is_stale), clears it on the way out — executor
        threads are pooled and reused — and always delivers the unguarded
        exit notification so the in-flight count stays truthful.
        """
        self._worker_gen.gen = gen
        try:
            work()
        finally:
            self._worker_gen.gen = None
            self._dispatch_ui_unguarded(self._worker_thread_exited, gen)

    def _worker_thread_exited(self, gen: int) -> None:
        """App-thread bookkeeping for a user-work thread that fully unwound.

        Deliberately UNGUARDED: this is the one callback a stale (cancelled)
        worker may still deliver, and it only maintains counters and the
        queue — it never writes output on the OLD run's behalf. When the
        last in-flight thread exits while the status is still "cancelled",
        true idle is restored quietly (re-arming background auto-sync, which
        waits for genuine idleness). Restoring true idle is also the moment
        the single-slot queue may drain: _maybe_dequeue re-checks _is_idle,
        so a stale exit that does NOT restore idle (another worker still in
        flight, or a new command already running) dequeues nothing.
        """
        self._inflight_workers = max(0, self._inflight_workers - 1)
        if gen == self._run_generation:
            self._active_worker = None
        if self._inflight_workers == 0 and self.status == "cancelled":
            self._set_idle()
        self._maybe_dequeue()

    def action_cancel_command(self) -> None:
        """Esc: cancel the running user command AND drop the queued one;
        a no-op when idle with an empty queue.

        Thread workers cannot be force-killed, so cancellation is
        cooperative: cancel() flags the worker (any handler polling
        get_current_worker().is_cancelled can stop early) and the generation
        bump makes every UI callback the old run marshals stale — dropped by
        _run_if_current — so late output can never reach the scrollback and
        the finished/failed lines, toasts and status flips of the cancelled
        run are all suppressed. The thread itself may still finish its
        current step (e.g. a network call) in the background;
        _worker_thread_exited restores true idle once it unwinds.
        """
        worker = self._active_worker
        if worker is None:
            # No live user worker to cancel. A command queued behind
            # BACKGROUND work (auto-sync, or a cancelled worker still
            # unwinding) can still be discarded here; with an empty queue
            # this stays the strict no-op it always was.
            if self._queued_command is not None:
                dropped_name = self._queued_command[0]
                self._queued_command = None
                self._update_top_bar()
                self.append_log(Text(f"Dropped queued /{dropped_name}.", style="yellow"))
            return  # idle (pushed screens own their Esc before this binding)
        dropped = self._queued_command[0] if self._queued_command is not None else None
        self._queued_command = None  # Esc clears the queue along with the run
        label = self.status
        if label.startswith("running "):
            label = label[len("running ") :]
        self._active_worker = None
        self._run_generation += 1  # everything the old run marshals is now stale
        worker.cancel()
        if not getattr(self.cli, "last_search_preview_persist", False):
            clear_preview()
        self._run_started = None
        self._stage = ""
        self._last_run_note = f"last: {label} cancelled"
        self.status = "cancelled"
        self.append_log(
            Text(
                f"Cancelled {label}. The worker thread can't be force-stopped, so a "
                "step already in flight (e.g. a network call) may finish in the "
                "background; its output is discarded, and new commands wait "
                "until it unwinds.",
                style="yellow",
            )
        )
        if dropped is not None:
            # Say BOTH: the cancel line above covers the running command; this
            # one covers the queued command that will now never start.
            self.append_log(Text(f"Dropped queued /{dropped} as well.", style="yellow"))

    def _execute_command(self, command: str, args: object) -> None:
        failed = False
        try:
            rc = dispatch_command(self.cli, command, args)
            if rc != 0:
                failed = True
                self._dispatch_ui(
                    self.append_log,
                    Text(
                        f"/{command} exited with errors — run /debug errors for details.",
                        style="red",
                    ),
                )
            elif self._command_error_count:
                # rc==0 but ERROR-level records fired mid-run: the command
                # succeeded only nominally (e.g. partial failures that still
                # return 0). Render the honest red line. This branch is
                # rc==0-only, so it can never double-print with the rc!=0
                # line above.
                failed = True
                count = self._command_error_count
                noun = "error" if count == 1 else "errors"
                self._dispatch_ui(
                    self.append_log,
                    Text(
                        f"/{command} exited with errors ({count} {noun} logged) "
                        "— run /debug errors for details.",
                        style="red",
                    ),
                )
        except Exception as exc:
            failed = True
            logger.exception("Command failed: /%s", command)
            self._dispatch_ui(
                self.append_log,
                error_panel(f"Command /{command} failed: {exc}"),
            )
        finally:
            # Both are stale-guarded: a cancelled run's completion lines,
            # toast and _post_command (status flip + "finished" line) are
            # all suppressed — the cancel line already told the truth.
            self._notify_command_result(command, failed)
            self._dispatch_ui(self._post_command, command, failed)

    def _notify_command_result(self, command: str, failed: bool) -> None:
        """Emit at most one toast per command: error on failure, info when slow."""
        if self._calling_thread_is_stale():
            return  # cancelled run: no late toast
        elapsed = 0.0
        if self._run_started is not None:
            elapsed = time.monotonic() - self._run_started
        try:
            if failed:
                self.notify(f"/{command} exited with errors", severity="error")
            elif elapsed > 10:
                self.notify(
                    f"/{command} finished in {self._format_elapsed(elapsed)}",
                    severity="information",
                )
        except Exception:
            # Toasts are best-effort; never let them break command teardown.
            logger.debug("Toast notification failed for /%s", command, exc_info=True)

    # `failed` defaults to False because several tests (and the wizard paths)
    # call _post_command directly with just the command name.
    def _post_command(self, command: str, failed: bool = False) -> None:
        # Dismiss the transient preview pane — unless the command flagged that
        # the preview is the only copy of its results (SEARCH_FINAL_TABLE_MODE=none
        # writes no scrollback table, so clearing would lose them).
        if not getattr(self.cli, "last_search_preview_persist", False):
            clear_preview()
        if self._run_started is not None:
            elapsed = self._format_elapsed(time.monotonic() - self._run_started)
            self._last_run_note = f"last: /{command} {elapsed}"
            if not failed:
                # A failed command already rendered its red completion line in
                # _execute_command — the dim "finished" line would contradict it.
                self.append_log(Text(f"/{command} finished in {elapsed}", style="dim"))
        if (
            command == "search"
            and self.cli.last_search_results
            and not getattr(self.cli, "last_search_handled", False)
        ):
            self._prompt_search_followup()
        self._set_idle()

    def _set_idle(self) -> None:
        self._run_started = None
        self._stage = ""
        self.status = "idle"

    # ------------------------------------------------------------------
    # Background listen-sync (quiet, cursor-based)
    # ------------------------------------------------------------------

    @staticmethod
    def _auto_sync_minutes() -> int:
        """Auto-sync interval in minutes (TUNR_AUTO_SYNC_MINUTES; 0 disables)."""
        try:
            return int(os.environ.get("TUNR_AUTO_SYNC_MINUTES", "30"))
        except ValueError:
            logger.warning("Invalid TUNR_AUTO_SYNC_MINUTES; using the 30-minute default.")
            return 30

    def _schedule_auto_sync(self) -> None:
        """Arm the periodic quiet listen-sync plus a one-shot warm-up sync."""
        minutes = self._auto_sync_minutes()
        if minutes <= 0:
            return
        self.set_interval(60 * minutes, self._maybe_auto_sync)
        # One-shot initial sync shortly after launch so the ledger is fresh
        # without waiting a full interval.
        self.set_timer(3, self._maybe_auto_sync)

    def _maybe_auto_sync(self) -> None:
        """Run a quiet listen-sync iff nothing else is using the connection.

        Takes the SAME `status` gate `_run_command` checks (both run on the
        app thread, so check-then-set cannot race), and additionally bails
        while a pushed screen (e.g. /dash) is active — the dashboard queries
        the shared sqlite connection synchronously on the app thread, so a
        concurrent worker-thread sync would violate the serialized-use
        contract documented in storage/db.py. Together these guarantee user
        work and the auto-sync never overlap on the single shared connection.
        """
        # Strict "idle" on purpose (NOT _is_idle): after an Esc-cancel the
        # status reads "cancelled" while the cancelled worker's thread may
        # still be unwinding on the shared sqlite connection. Background work
        # can afford to wait for genuine idleness (_worker_thread_exited
        # restores it), so it never risks overlapping that tail.
        if self._setup_mode or self.status != "idle":
            return
        if self._queued_command is not None:
            # Defence-in-depth: a queued user command owns the next idle slot
            # (dequeue is synchronous with idle restoration, so this state
            # should be unobservable — never bet the gate on "should").
            return
        if len(self.screen_stack) > 1:
            return  # a modal screen (/dash) is reading the shared connection
        if self._auto_sync_scope_blocked:
            # Stand down after an insufficient-scope failure — but self-clear
            # the moment a cached token granting every required scope appears
            # (the user ran /auth-reset --yes and re-authorized). A live client
            # picks a re-cached token up automatically, so resuming is safe.
            token_info = get_cached_token_info()
            if token_info is None or missing_scopes(token_info.get("scope")):
                return
            self._auto_sync_scope_blocked = False
            logger.info("Auto-sync resuming: cached token now grants the required scopes.")
        if getattr(self.cli, "_spotify", None) is None and not get_cached_token_info():
            # No cached token: touching cli.spotify would launch the BLOCKING
            # interactive OAuth flow from a background worker (surprise
            # browser popup + a worker that never returns = wedged gate).
            # Skip quietly until the user authenticates.
            if not self._auto_sync_token_warned:
                self._auto_sync_token_warned = True
                logger.info("Auto-sync skipped: no cached Spotify token yet (run /auth-status).")
            return
        self.status = "auto-sync"
        self.run_worker(self._auto_sync_worker, thread=True)

    def _auto_sync_worker(self) -> None:
        try:
            self.cli.sync_listen_history(quiet=True)
        except Exception as exc:
            # A failure mid-write would leave an open transaction on the
            # long-lived shared connection — the next unrelated command's
            # commit would silently persist the partial sync. Roll it back
            # (best-effort) before logging.
            try:
                self.cli.repos.conn.rollback()
            except Exception:
                logger.debug("Rollback after failed auto-sync failed.", exc_info=True)
            hint = scope_error_hint(exc)
            if hint is not None:
                # A stale-scope token can never succeed on retry: log the
                # actionable hint once and stand down (the blocked flag is
                # self-clearing — _maybe_auto_sync resumes when a cached token
                # with the required scopes appears).
                self._auto_sync_scope_blocked = True
                logger.warning("Background listen-sync blocked by a missing scope. %s", hint)
            elif not self._auto_sync_warned:
                self._auto_sync_warned = True
                logger.warning(
                    "Background listen-sync failed; retrying quietly each interval.",
                    exc_info=True,
                )
            else:
                logger.debug("Background listen-sync failed again.", exc_info=True)
        finally:
            self._dispatch_ui(self._finish_auto_sync)

    def _finish_auto_sync(self) -> None:
        # Release the gate only if auto-sync still holds it.
        if self.status == "auto-sync":
            self.status = "idle"
        # A command submitted while auto-sync held the gate waits in the
        # single-slot queue; auto-sync teardown is an idle-restoration point,
        # so drain it here (same _is_idle re-check as everywhere else).
        self._maybe_dequeue()

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        if total < 60:
            return f"{total}s"
        minutes, secs = divmod(total, 60)
        if minutes < 60:
            return f"{minutes}m{secs:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes:02d}m"

    def _show_welcome(self) -> None:
        welcome = Text()
        welcome.append("Welcome to Tunr\n", style="bold")
        welcome.append("Launch any time with: tunr\n", style="dim")
        welcome.append("Commands are slash-prefixed. Type /help for the list.\n", style="dim")
        self.append_log(Panel(welcome, title="Welcome", border_style=ACCENT_BLUE))

    @staticmethod
    def _help_table(title: str, rows: "list[tuple[str, str]]") -> Table:
        t = Table(title=title, title_justify="left", box=box.SIMPLE, show_header=False, expand=True)
        # No fixed width on the command column: it sizes to the longest name so
        # commands like /restore-previous-rotation aren't truncated.
        t.add_column("Command", style=ACCENT_BLUE, no_wrap=True)
        t.add_column("Description", overflow="fold", no_wrap=False)
        for name, help_text in rows:
            t.add_row(name, help_text or "")
        return t

    def _show_help(self, show_all: bool = False) -> None:
        # Session / meta commands first (TUI-only; always available).
        self.append_log(
            self._help_table(
                "Session",
                [
                    ("/help, /?", "Show this help (/help all includes legacy commands)"),
                    ("/setup", "Show first-time setup instructions"),
                    ("/env, /keys", "Show detected environment keys"),
                    ("/debug", "Show debug info (errors, last, track <id|rank>)"),
                    ("/errors", "Show error log (alias for /debug errors)"),
                    ("/expand, /search-more", "Expand the last search"),
                    ("/clear, /cls", "Clear the output pane"),
                    ("/quit, /exit", "Exit the app"),
                ],
            )
        )
        if self._setup_mode:
            return

        summaries = dict(self._command_summaries())
        mapped = set(HELP_LEGACY)
        for title, names in HELP_GROUPS:
            mapped.update(names)
            # Meta (TUI-only) commands like /dash can live in a task group too;
            # their one-liners come from META_COMMAND_HELP instead of argparse.
            rows = [
                (f"/{name}", summaries.get(name) or META_COMMAND_HELP[name])
                for name in names
                if name in summaries or name in META_COMMAND_HELP
            ]
            if rows:
                self.append_log(self._help_table(title, rows))

        if show_all:
            legacy_rows = [
                (f"/{name}", summaries[name]) for name in summaries if name in HELP_LEGACY
            ]
            if legacy_rows:
                self.append_log(self._help_table("Legacy", legacy_rows))

        # Any command not placed in a group (e.g. a newly added one) still shows up.
        misc_rows = [(f"/{name}", summaries[name]) for name in summaries if name not in mapped]
        if misc_rows:
            self.append_log(self._help_table("Other", misc_rows))

        if not show_all and any(name in summaries for name in HELP_LEGACY):
            self.append_log(Text("Type /help all to show legacy commands.", style="dim"))
        self.append_log(
            Text('Example: /update "My Playlist" --count 10 --fresh-days 21', style="dim")
        )

    def _command_summaries(self) -> Iterable[Tuple[str, str]]:
        for action in self.parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for choice in action._choices_actions:
                    name = choice.dest
                    # `rotate-played` is a deprecated alias for `rotate`; keep it
                    # usable but out of the advertised command list.
                    if name in {"interactive", "debug", "rotate-played"}:
                        continue
                    yield name, choice.help or ""

    @staticmethod
    def _meta_command_names() -> List[str]:
        return [name for name in META_COMMAND_HELP if name != "?"]

    def _build_suggester(self) -> TunrSuggester:
        """Assemble the ghost-text suggester from the parser + meta inventory.

        History is handed over as a provider callable because
        ``_append_history`` REBINDS ``self._history`` on truncation. Note that
        history navigation (`_set_input_value`) also writes recalled lines
        into the Input, which triggers the suggester on them — deliberate:
        a recalled line may show a longer, more recent line as ghost text,
        but never suggests itself (pinned by tests).
        """
        command_names = [name for name, _ in self._command_summaries()]
        flag_map = {}
        for name in command_names:
            sub = self._find_subparser(name)
            if sub is not None:
                flag_map[name] = [
                    option for action in sub._actions for option in action.option_strings
                ]
        return TunrSuggester(
            commands=command_names + self._meta_command_names(),
            flags=flag_map,
            history=lambda: self._history,
        )

    def _find_subparser(self, name: str) -> Optional[argparse.ArgumentParser]:
        for action in self.parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action.choices.get(name)
        return None

    # ------------------------------------------------------------------
    # Command palette (ctrl+p)
    # ------------------------------------------------------------------

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Stock system commands minus the theme switcher.

        The default "Theme" entry would knock the session off the OP-1
        palette that the App CSS and the Rich-side twin in ui.py both assume.
        Filter by title so this stays correct regardless of the order the
        upstream commands are yielded in.
        """
        for command in super().get_system_commands(screen):
            if "theme" in command.title.lower():
                continue
            yield command

    @staticmethod
    def _subparser_needs_argument(sub: argparse.ArgumentParser) -> bool:
        """True when a command takes ANY positional or required option.

        Safest-default classification for the palette: even an optional
        positional (nargs='?') counts, because it is often effectively
        required (e.g. /backup <name>). Only provably no-arg commands are
        submitted directly; everything else is preloaded into the input.
        """
        for action in sub._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            if not action.option_strings:
                return True  # positional, even an optional one
            if action.required:
                return True
        return False

    def _palette_commands(self) -> List[PaletteCommand]:
        """Palette inventory: advertised registry commands + meta commands."""
        commands: List[PaletteCommand] = []
        seen = set()
        for name, help_text in self._command_summaries():
            seen.add(name)
            sub = self._find_subparser(name)
            needs_argument = sub is None or self._subparser_needs_argument(sub)
            commands.append(PaletteCommand(name, help_text, needs_argument))
        for name in self._meta_command_names():
            if name in seen:
                continue
            # Meta commands are all callable with no arguments.
            commands.append(PaletteCommand(name, META_COMMAND_HELP[name], False))
        return commands

    def _palette_submit(self, name: str) -> None:
        """Palette callback for a provably no-arg command.

        Submits through _submit_text so the setup-mode and busy gates apply
        exactly as they do for typed input.
        """
        self._submit_text(f"/{name}")

    def _palette_insert(self, name: str) -> None:
        """Palette callback for an arg-taking command.

        Preloads ``/name `` into the input and focuses it so the user can
        finish the arguments; nothing is submitted or dispatched.
        """
        self._write_input(f"/{name} ")
        self._focus_input()

    def _focus_input(self) -> None:
        try:
            self.query_one(Input).focus()
        except Exception:
            logger.debug("Could not focus the command input", exc_info=True)

    def _show_command_help(self, raw_name: str) -> None:
        name = raw_name.strip().lstrip("/").strip()
        if not name:
            self._show_help()
            return
        meta = META_COMMAND_HELP.get(name)
        if meta is not None:
            self.append_log(Panel(Text(meta), title=f"/{name}", border_style=ACCENT_BLUE))
            return
        sub = self._find_subparser(name)
        if sub is not None:
            self.append_log(
                Panel(Text(sub.format_help()), title=f"/{name}", border_style=ACCENT_BLUE)
            )
            return
        candidates = [cmd for cmd, _ in self._command_summaries()]
        candidates.extend(self._meta_command_names())
        message = unknown_command_message(name, candidates)
        self.append_log(error_panel(message))

    def _update_top_bar(self) -> None:
        if not self._mounted:
            return
        top_bar = self.query_one("#top_bar", Static)
        top_bar.update(self._render_top_bar())
        self._update_setup_screen()

    def _update_input_placeholder(self) -> None:
        if not self._mounted:
            return
        command_input = self.query_one(Input)
        if self._setup_mode:
            command_input.placeholder = "setup required. type /setup"
        else:
            command_input.placeholder = "type /help for commands"

    def _render_top_bar(self):
        width = self.size.width or 0
        label_text = "tunr"
        content_width = max(0, width - 4)
        max_status_width = max(0, content_width - len(label_text) - 1)
        status_style = "green" if self.status == "idle" else "yellow"
        if self._setup_mode:
            status_style = "red"
        if max_status_width < 8:
            return Text(label_text, style=SUBSECTION_STYLE)
        status_label = self.status
        if self._stage and self.status != "idle":
            # Live stage readout, e.g. "running /search · extract 87/120".
            status_label = f"{status_label} · {self._stage}"
        if self._queued_command is not None and self.status != "idle":
            # The single-slot queue is visible for its whole lifetime,
            # e.g. "running /search · queued: /stats".
            status_label = f"{status_label} · queued: /{self._queued_command[0]}"
        if self._spinner_timer is not None:
            status_label = f"{self.SPINNER_FRAMES[self._spinner_index]} {status_label}"
        if self.status != "idle" and self._run_started is not None:
            elapsed = self._format_elapsed(time.monotonic() - self._run_started)
            status_label = f"{status_label} • {elapsed}"
        elif self.status == "idle" and self._last_run_note:
            status_label = f"idle · {self._last_run_note}"
        status_text = Text(status_label, style=status_style)
        status_text.truncate(max_status_width, overflow="ellipsis")
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")
        table.add_row(Text(label_text, style=SUBSECTION_STYLE), status_text)
        return table

    def _start_spinner(self) -> None:
        if self._spinner_timer is not None or not self._mounted:
            return
        self._spinner_timer = self.set_interval(0.2, self._tick_spinner)

    def _stop_spinner(self) -> None:
        if self._spinner_timer is None:
            return
        try:
            self._spinner_timer.stop()
        finally:
            self._spinner_timer = None
            self._spinner_index = 0
            self._update_top_bar()

    def _tick_spinner(self) -> None:
        self._spinner_index = (self._spinner_index + 1) % len(self.SPINNER_FRAMES)
        self._update_top_bar()

    def _refresh_env_status(self) -> None:
        status = {}
        for key in SPOTIFY_REQUIRED_KEYS:
            status[key] = bool(os.getenv(key))
        status["ANTHROPIC_API_KEY"] = bool(os.getenv("ANTHROPIC_API_KEY"))
        status["OPENAI_API_KEY"] = bool(os.getenv("OPENAI_API_KEY"))
        self._env_status = status
        self._missing_spotify_keys = [key for key in SPOTIFY_REQUIRED_KEYS if not status.get(key)]
        prev_setup = self._setup_mode
        self._setup_mode = bool(self._missing_spotify_keys)
        if self._setup_mode and self.status == "idle":
            self.status = "setup required"
        elif not self._setup_mode and self.status == "setup required":
            self.status = "idle"
        if prev_setup != self._setup_mode:
            self._update_setup_screen()
            self._update_top_bar()
            if not self._setup_mode:
                self.append_log(Text("Setup complete. Type /help to continue.", style="green"))

    def _show_debug_errors(self) -> None:
        if not self._error_log:
            self.append_log(Text("No errors captured yet.", style="dim"))
            return
        text = Text()
        text.append("Copy/paste the errors below:\n\n", style="bold")
        for entry in reversed(self._error_log):
            text.append(entry.rstrip())
            text.append("\n\n")
        self.append_log(Panel(text, title="Debug Log", border_style="red"))

    def _handle_debug(self, raw: str) -> None:
        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            self.append_log(error_panel(f"Invalid debug command: {exc}"))
            return
        if not tokens:
            return
        if len(tokens) == 1 or tokens[1] in {"errors", "error"}:
            self._show_debug_errors()
            return
        subcommand = tokens[1].lower()
        if subcommand in {"last", "search"}:
            self._show_debug_last()
            return
        if subcommand == "track":
            track_id = " ".join(tokens[2:]).strip()
            if not track_id:
                self.append_log(Text("Usage: /debug track <track_id>", style="yellow"))
                return
            self._show_debug_track(track_id)
            return
        self.append_log(Text("Usage: /debug [errors|last|track <id>]", style="yellow"))

    def _show_debug_last(self) -> None:
        if not self.cli.last_search_query:
            self.append_log(Text("No previous search to inspect.", style="dim"))
            return

        payload = self.cli.debug_last_search()
        if payload:
            run = payload.get("run") or {}
            candidates = payload.get("candidates") or []
            summary = payload.get("summary") or {}
            rows = [
                ["Query", self.cli.last_search_query],
                ["Run ID", run.get("run_id")],
                ["Results", len(candidates)],
                ["Expanded", "yes" if self.cli.last_search_expanded else "no"],
                ["Provider", run.get("provider") or "combined"],
                ["Status", run.get("status") or "unknown"],
                ["Cached", "yes" if summary.get("cached") else "no"],
                ["Model", summary.get("model_name") or "unknown"],
                ["Avg strict ratio", f"{summary.get('avg_strict_ratio', 0):.2f}"],
                ["Missing context", summary.get("missing_context", 0)],
            ]
            score_config = summary.get("score_config") or {}
            if score_config:
                rows.append(
                    [
                        "Score Config",
                        (
                            f"base {score_config.get('base_weight', 0):.2f} / "
                            f"strict {score_config.get('strict_weight', 0):.2f} / "
                            f"source {score_config.get('source_weight', 0):.2f} / "
                            f"year {score_config.get('year_weight', 0):.2f} / "
                            f"tol {score_config.get('year_tolerance', 0)} / "
                            f"cap {score_config.get('source_cap', 0)} / "
                            f"target {score_config.get('year_target') or '-'}"
                        ),
                    ]
                )
            section("Debug", "Last Search")
            key_value_table(rows)
            if candidates:
                preview_rows = []
                for idx, candidate in enumerate(candidates[:10], 1):
                    track = candidate.get("track") or {}
                    track_id = candidate.get("track_id")
                    artist_label = track.get("artist_name") or track.get("artist_id", "")
                    label = f"{track.get('name', '')} — {artist_label}".strip(" —")
                    preview_rows.append([idx, label, track_id])
                subsection("Top Results (IDs)")
                table(["#", "Track", "Track ID"], preview_rows)
                info("Use /debug track <id> or /debug track <rank> to inspect a specific entry.")
            return

        results = self.cli.last_search_results or []
        providers = sorted({p for item in results for p in (item.get("providers") or [])})
        rows = [
            ["Query", self.cli.last_search_query],
            ["Results", len(results)],
            ["Expanded", "yes" if self.cli.last_search_expanded else "no"],
            ["Providers", ", ".join(providers) if providers else "unknown"],
            ["Metrics", ", ".join(self.cli.last_search_metrics or []) or "none"],
        ]
        section("Debug", "Last Search")
        key_value_table(rows)
        if results:
            preview_rows = []
            for idx, item in enumerate(results[:10], 1):
                song = item.get("song") or item.get("name") or ""
                artist = item.get("artist") or ""
                track_id = item.get("track_id") or f"{artist.lower()}|||{song.lower()}"
                preview_rows.append([idx, f"{song} — {artist}", track_id])
            if preview_rows:
                subsection("Top Results (IDs)")
                table(["#", "Track", "Track ID"], preview_rows)
                info("Use /debug track <id> or /debug track <rank> to inspect a specific entry.")

    def _show_debug_track(self, track_id: str) -> None:
        raw_target = track_id.strip()
        results = self.cli.last_search_results or []

        if raw_target.isdigit():
            if not results:
                self.append_log(Text("No previous search to inspect.", style="dim"))
                return
            rank = int(raw_target)
            if rank < 1 or rank > len(results):
                self.append_log(Text(f"Rank out of range. Valid: 1-{len(results)}", style="yellow"))
                return
            item = results[rank - 1]
            song = item.get("song") or item.get("name") or ""
            artist = item.get("artist") or ""
            if song and artist:
                track_id = f"{artist.lower()}|||{song.lower()}"
            else:
                track_id = raw_target
        else:
            track_id = raw_target

        target = track_id.lower()
        found = None
        resolved_id = None
        for item in results:
            song = item.get("song") or item.get("name") or ""
            artist = item.get("artist") or ""
            if not song or not artist:
                continue
            candidate_id = f"{artist.lower()}|||{song.lower()}"
            if candidate_id == target:
                found = item
                resolved_id = candidate_id
                break

        if found:
            rows = [
                ["Track ID", resolved_id],
                ["Run ID", self.cli.last_search_run_id or "unknown"],
                ["Cached Run", "yes" if self.cli.last_search_cached else "no"],
                ["Song", found.get("song") or found.get("name") or ""],
                ["Artist", found.get("artist") or ""],
                ["Year", found.get("year") or "-"],
                ["Providers", ", ".join(found.get("providers") or []) or "unknown"],
                [
                    "Score",
                    f"{found.get('score', 0):.3f}"
                    if isinstance(found.get("score"), (int, float))
                    else found.get("score"),
                ],
                [
                    "Strict Ratio",
                    f"{found.get('strict_ratio', 0):.2f}"
                    if isinstance(found.get("strict_ratio"), (int, float))
                    else found.get("strict_ratio"),
                ],
            ]
            section("Debug", "Track")
            key_value_table(rows)
            sources = found.get("sources") or []
            if sources:
                subsection("Sources")
                table(["#", "Source"], [[i, s] for i, s in enumerate(sources, 1)])

        debug_payload = self.cli.debug_track(target)
        if debug_payload:
            context = debug_payload.get("context") or {}
            sources = debug_payload.get("sources") or []
            embedding = debug_payload.get("embedding") or {}
            listens = debug_payload.get("listens") or []
            if context:
                fields_payload = context.get("fields_json")
                sources_payload = context.get("sources_json")
                try:
                    if isinstance(fields_payload, str):
                        fields_payload = json.loads(fields_payload)
                except Exception as exc:
                    logger.debug("Failed to parse debug JSON: %s", exc)
                try:
                    if isinstance(sources_payload, str):
                        sources_payload = json.loads(sources_payload)
                except Exception as exc:
                    logger.debug("Failed to parse debug JSON: %s", exc)
                subsection("Context")
                json_output(
                    {
                        "context_text": context.get("context_text"),
                        "strict_text": context.get("strict_text"),
                        "lenient_text": context.get("lenient_text"),
                        "strict_ratio": context.get("strict_ratio"),
                        "fields": fields_payload,
                        "sources": sources_payload,
                    }
                )
            if sources:
                subsection("Sources (DB)")

                def _shorten(value: object, limit: int = 120) -> str:
                    text = str(value or "").replace("\n", " ").strip()
                    if len(text) <= limit:
                        return text
                    return f"{text[: limit - 3]}..."

                table(
                    ["#", "URL", "Title", "Snippet", "Provider", "Strict"],
                    [
                        [
                            i,
                            s.get("url") or "",
                            _shorten(s.get("title")),
                            _shorten(s.get("snippet")),
                            s.get("provider") or "",
                            "yes" if s.get("is_strict") else "no",
                        ]
                        for i, s in enumerate(sources, 1)
                    ],
                )
            if embedding:
                subsection("Embedding")
                key_value_table(
                    [
                        ["Model", embedding.get("model_name") or ""],
                        ["Dimensions", embedding.get("embedding_dim") or ""],
                        [
                            "Norm",
                            f"{embedding.get('embedding_norm', 0):.4f}"
                            if embedding.get("embedding_norm") is not None
                            else "",
                        ],
                        ["Created", embedding.get("created_at") or ""],
                    ]
                )
            if listens:
                subsection("Listen Events")
                table(
                    ["#", "Played At", "Source"],
                    [
                        [i, event.get("played_at") or "", event.get("source") or ""]
                        for i, event in enumerate(listens[:10], 1)
                    ],
                )
            return

        try:
            song = self.cli.db.get_song_by_id(target)
        except Exception as exc:
            logger.debug("DB lookup failed for %s: %s", target, exc)
            song = None
        if song:
            rows = [
                ["Track ID", target],
                ["Song", song.name],
                ["Artist", song.artist],
                ["Spotify URI", song.spotify_uri or "Unknown"],
                ["First Added", song.first_added.isoformat() if song.first_added else "Unknown"],
            ]
            section("Debug", "Track (Database)")
            key_value_table(rows)
            return

        warning(f"No track found for id: {track_id}")

    def _env_table(self) -> Table:
        table = Table(
            title="Environment Keys",
            box=box.SIMPLE,
            show_header=True,
            header_style="bold",
            expand=True,
        )
        table.add_column("Key", overflow="fold")
        table.add_column("Required", justify="center")
        table.add_column("Status", justify="center")

        for key in SPOTIFY_REQUIRED_KEYS:
            table.add_row(key, "Yes", "SET" if self._env_status.get(key) else "MISSING")

        for key in SEARCH_OPTIONAL_KEYS:
            table.add_row(key, "No", "SET" if self._env_status.get(key) else "MISSING")

        return table

    def _show_env(self) -> None:
        self._refresh_env_status()
        if self._setup_mode:
            self._update_setup_screen()
            return
        self.append_log(self._env_table())
        providers = sorted(detect_search_commands().keys())
        if providers:
            self.append_log(Text(f"Deep search providers: {', '.join(providers)}", style="dim"))
        else:
            self.append_log(Text("Deep search providers: none detected", style="dim"))

    def _show_setup(self) -> None:
        self._refresh_env_status()
        if self._setup_mode:
            self._update_setup_screen()
            return
        self.append_log(self._render_setup_content())

    def _render_setup_content(self) -> Group:
        setup = Text()
        setup.append("First-time setup\n", style="bold")
        setup.append("1) Create config/.env with your Spotify keys:\n", style="dim")
        setup.append("   SPOTIFY_CLIENT_ID=...\n", style="dim")
        setup.append("   SPOTIFY_CLIENT_SECRET=...\n", style="dim")
        setup.append("   SPOTIFY_REDIRECT_URI=http://localhost:8888/callback\n", style="dim")
        setup.append("2) Restart tunr after editing .env\n", style="dim")
        setup.append(
            "3) Optional: set ANTHROPIC_API_KEY and/or OPENAI_API_KEY for /search\n", style="dim"
        )
        providers = sorted(detect_search_commands().keys())
        provider_text = Text(
            f"Deep search providers: {', '.join(providers) if providers else 'none detected'}",
            style="dim",
        )
        return Group(
            Panel(setup, title="Setup", border_style=ACCENT_BLUE), self._env_table(), provider_text
        )

    def _prompt_search_followup(self) -> None:
        self._pending_action = "search_confirm"
        self._pending_payload = {
            "track_ids": self.cli.last_search_track_ids or [],
            "query": self.cli.last_search_query,
        }
        self.append_log(
            Text(
                "Cached. Mark these recommendations and/or create a playlist? (yes/no)",
                style="bold",
            )
        )
        self.append_log(Text("Or run /expand to broaden the search.", style="dim"))

    def _handle_pending_input(self, raw: str) -> None:
        value = raw.strip().lower()
        if self._pending_action == "search_confirm":
            if value in {"yes", "y"}:
                self._pending_action = "search_action"
                self.append_log(Text("Choose: db, playlist, both, or cancel", style="bold"))
                return
            if value in {"no", "n"}:
                self.append_log(
                    Text("No problem. Try /search <criteria> or /expand to broaden.", style="dim")
                )
                self._clear_pending()
                return
            self.append_log(Text("Please answer yes or no.", style="yellow"))
            return

        if self._pending_action == "search_action":
            if value in {"db", "database"}:
                self._apply_search_results(mode="db")
                return
            if value in {"playlist", "pl"}:
                self._pending_action = "search_playlist_name"
                self._pending_payload["mode"] = "playlist"
                self.append_log(Text("Playlist name?", style="bold"))
                return
            if value in {"both", "all"}:
                self._pending_action = "search_playlist_name"
                self._pending_payload["mode"] = "both"
                self.append_log(Text("Playlist name?", style="bold"))
                return
            if value in {"cancel", "no", "n"}:
                self.append_log(
                    Text("Cancelled. Try /search <criteria> to run again.", style="dim")
                )
                self._clear_pending()
                return
            self.append_log(Text("Please choose db, playlist, both, or cancel.", style="yellow"))
            return

        if self._pending_action == "search_playlist_name":
            if not value:
                self.append_log(Text("Please enter a playlist name.", style="yellow"))
                return
            mode = self._pending_payload.get("mode", "playlist")
            self._apply_search_results(mode=mode, playlist_name=raw.strip())
            return

    def _apply_search_results(
        self,
        mode: str,
        playlist_name: Optional[str] = None,
        track_ids: Optional[List[str]] = None,
    ) -> None:
        """Route accepted tracks into the existing mark/playlist worker.

        Two callers: the typed wizard (track_ids=None → the pending payload's
        full result set) and the /results browser's dismiss callback (an
        explicit row-level subset).
        """
        if self._refuse_if_busy():
            return
        if track_ids is None:
            track_ids = self._pending_payload.get("track_ids") or []
        if not track_ids:
            self.append_log(Text("No search results available.", style="yellow"))
            self._clear_pending()
            return

        def _worker() -> None:
            try:
                if mode in {"db", "both"}:
                    self.cli.mark_search_tracks(track_ids, status="accepted")
                if mode in {"playlist", "both"}:
                    if not playlist_name:
                        return
                    self.cli.add_search_to_playlist(playlist_name, track_ids)
            finally:
                self._dispatch_ui(self._set_idle)

        self._run_started = time.monotonic()
        self._start_user_worker("applying search results", _worker)
        self._clear_pending()

    def _open_dashboard(self) -> None:
        """Push the /dash screen; refocus the command input when it closes.

        Gated on the same `status` check as `_run_command`: the dashboard
        queries the shared sqlite connection synchronously on the app thread,
        so it must not open while a worker (command or auto-sync) is mid-write.
        """
        if self._refuse_if_busy():
            return

        def _refocus(_result: object = None) -> None:
            try:
                self.query_one(Input).focus()
            except Exception:
                logger.debug("Could not refocus input after dashboard close", exc_info=True)

        self.push_screen(DashboardScreen(self.cli), callback=_refocus)

    def _open_results(self) -> None:
        """Push the /results browser; refocus the input and route any
        row-level action into the apply worker when it closes.

        Gated on the same `status` check as `_run_command` (and /dash): the
        screen reads the shared sqlite connection synchronously on the app
        thread (debug_track, spotify_url_for_track), so it must not open
        while a worker (command or auto-sync) is mid-write. Auto-sync itself
        bails while any screen is pushed (`screen_stack > 1`), so the
        contract holds for the screen's whole lifetime. Writes happen only
        AFTER dismissal, via the existing _apply_search_results worker.
        """
        if self._refuse_if_busy():
            return
        if not results_for_browse(self.cli):
            self.append_log(
                Text("No cached results to browse. Run /search or /find first.", style="yellow")
            )
            return

        def _on_close(action: Optional[ResultsAction] = None) -> None:
            try:
                self.query_one(Input).focus()
            except Exception:
                logger.debug("Could not refocus input after results close", exc_info=True)
            if action is None:
                return
            if action.mode == "prefill" and action.prefill:
                # Enter on a row: preload the seeded command for the user to
                # edit and submit — insert, never dispatch (the palette's
                # insert-vs-submit convention).
                self._write_input(action.prefill)
                return
            if action.track_ids:
                self._apply_search_results(
                    mode=action.mode,
                    playlist_name=action.playlist_name,
                    track_ids=action.track_ids,
                )

        self.push_screen(ResultsScreen(self.cli), callback=_on_close)

    def _expand_search(self) -> None:
        if self._setup_mode:
            self.append_log(Text("Finish setup before running searches.", style="yellow"))
            return
        if not self.cli.last_search_query:
            self.append_log(
                Text("No previous search to expand. Run /search <criteria> first.", style="yellow")
            )
            return
        if self._refuse_if_busy():
            return
        self.append_log(Text(f"Expanding search: {self.cli.last_search_query}", style="bold"))
        self._run_started = time.monotonic()
        self._start_user_worker("running /expand", self._execute_expand)

    def _execute_expand(self) -> None:
        try:
            self.cli.search_songs(self.cli.last_search_query, expanded=True)
        finally:
            self._dispatch_ui(self._set_idle)

    def _clear_pending(self) -> None:
        self._pending_action = None
        self._pending_payload = {}


def run_interactive() -> int:
    os.environ.setdefault("TUNR_INTERACTIVE", "1")
    parser = setup_parsers()
    cli = PlaylistCLI()
    app = PlaylistInteractiveApp(cli=cli, parser=parser)
    app.run()
    return 0
