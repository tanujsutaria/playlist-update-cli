from __future__ import annotations

import argparse
import logging
import os
import shlex
from typing import Iterable, Tuple, List, Optional

from rich.console import Group

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Input, RichLog, Static

from arg_parse import setup_parsers, parse_tokens
from main import PlaylistCLI, dispatch_command, configure_logging
from ui import set_output_sink
SPOTIFY_REQUIRED_KEYS = [
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "SPOTIFY_REDIRECT_URI",
]

SEARCH_OPTIONAL_KEYS = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
]


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
            elif record.levelno >= logging.WARNING:
                style = "yellow"
            elif record.levelno >= logging.INFO:
                style = "cyan"
            elif record.levelno >= logging.DEBUG:
                style = "dim"
            text = Text(message, style=style)
            self.app.call_from_thread(self.app.append_log, text)
        except Exception:
            self.handleError(record)


class PlaylistInteractiveApp(App):
    CSS = """
    Screen {
        background: #0b0f14;
        color: #e6edf3;
    }
    #top_bar {
        height: 1;
        padding: 0 2;
        background: #0f141a;
        color: #9da7b3;
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
    #setup_screen {
        padding: 2 4;
        width: 1fr;
        height: 1fr;
    }
    #command_input {
        dock: bottom;
        background: #0f141a;
        border: solid #2d333b;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear output"),
    ]

    status = reactive("Idle")

    def __init__(self, cli: PlaylistCLI, parser: argparse.ArgumentParser) -> None:
        super().__init__()
        self.cli = cli
        self.parser = parser
        self._history: List[str] = []
        self._history_index: Optional[int] = None
        self._pending_action: Optional[str] = None
        self._pending_payload: dict = {}
        self._missing_spotify_keys: List[str] = []
        self._env_status: dict = {}
        self._setup_mode = False
        self._mounted = False

    def compose(self) -> ComposeResult:
        yield Static(id="top_bar")
        with Container(id="body"):
            yield RichLog(id="output", highlight=False, markup=False)
            yield Static(id="setup_screen")
        yield Input(placeholder="Type /help for commands", id="command_input")

    def on_mount(self) -> None:
        self._mounted = True
        set_output_sink(self._emit_renderable)
        configure_logging(handler=UILogHandler(self))
        self._refresh_env_status()
        self._update_top_bar()
        self._show_welcome()
        if self._missing_spotify_keys:
            self._show_setup()
        self.query_one(Input).focus()
        self._update_top_bar()

    def on_shutdown(self) -> None:
        self._mounted = False
        set_output_sink(None)

    def on_resize(self) -> None:
        self._update_top_bar()
        self.refresh(layout=True)

    def _update_setup_screen(self) -> None:
        if not self._mounted:
            return
        output = self.query_one(RichLog)
        setup_screen = self.query_one("#setup_screen", Static)
        if self._setup_mode:
            output.display = False
            setup_screen.display = True
            setup_screen.update(self._render_setup_content())
        else:
            setup_screen.display = False
            output.display = True
        self._update_input_placeholder()

    def watch_status(self, value: str) -> None:
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
        self._history.append(raw)
        self._history_index = None
        self.append_log(Text(f"> {raw}", style="bold"))
        self._handle_command(raw)

    def action_clear_log(self) -> None:
        self.query_one(RichLog).clear()

    def action_quit(self) -> None:
        self.exit()

    def append_log(self, renderable) -> None:
        log = self.query_one(RichLog)
        log.write(renderable)

    def _emit_renderable(self, renderable) -> None:
        self.call_from_thread(self.append_log, renderable)

    def _history_prev(self) -> None:
        if not self._history:
            return
        if self._history_index is None:
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self._set_input_value(self._history[self._history_index])

    def _history_next(self) -> None:
        if self._history_index is None:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            value = self._history[self._history_index]
        else:
            self._history_index = None
            value = ""
        self._set_input_value(value)

    def _set_input_value(self, value: str) -> None:
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

        if text in ("help", "?"):
            self._show_help()
            return
        if text in ("setup",):
            self._show_setup()
            return
        if text in ("env", "keys"):
            self._show_env()
            return
        if text in ("expand", "search-more"):
            self._expand_search()
            return
        if text in ("clear", "cls"):
            self.action_clear_log()
            return
        if text in ("quit", "exit"):
            self.action_quit()
            return

        if self._setup_mode:
            missing = ", ".join(self._missing_spotify_keys)
            self.append_log(
                Panel(
                    Text(
                        f"Spotify keys missing: {missing}\nRun /setup for instructions.",
                        style="red",
                    ),
                    title="Setup Required",
                    border_style="red",
                )
            )
            return

        tokens = shlex.split(text)
        command, args, error = parse_tokens(tokens)
        if error:
            self.append_log(Panel(Text(error, style="red"), title="Error", border_style="red"))
            return
        if command == "interactive":
            self.append_log(Text("Already in interactive mode.", style="yellow"))
            return
        self._run_command(command, args)

    def _run_command(self, command: str, args: object) -> None:
        if self.status != "Idle":
            self.append_log(Text("Another command is already running.", style="yellow"))
            return
        self.status = f"Running /{command}"
        self.run_worker(lambda: self._execute_command(command, args), thread=True)

    def _execute_command(self, command: str, args: object) -> None:
        try:
            dispatch_command(self.cli, command, args)
        finally:
            self.call_from_thread(self._post_command, command)

    def _post_command(self, command: str) -> None:
        if command == "search" and self.cli.last_search_results:
            self._prompt_search_followup()
        self._set_idle()

    def _set_idle(self) -> None:
        self.status = "Idle"

    def _show_welcome(self) -> None:
        welcome = Text()
        welcome.append("Welcome to Tunr\n", style="bold")
        welcome.append("Launch any time with: tunr\n", style="dim")
        welcome.append("Commands are slash-prefixed. Type /help for the list.\n", style="dim")
        self.append_log(Panel(welcome, title="Welcome", border_style="cyan"))

    def _show_help(self) -> None:
        table = Table(title="Commands", box=box.SIMPLE, show_header=True, header_style="bold", expand=True)
        table.add_column("Command", style="cyan", overflow="fold", no_wrap=True, width=18)
        table.add_column("Description", overflow="fold", no_wrap=False)
        table.add_row("/help", "Show this help screen")
        table.add_row("/setup", "Show first-time setup instructions")
        table.add_row("/env", "Show detected environment keys")
        table.add_row("/expand", "Expand the last search")
        table.add_row("/clear", "Clear the output pane")
        table.add_row("/quit", "Exit the app")
        if not self._setup_mode:
            for name, help_text in self._command_summaries():
                table.add_row(f"/{name}", help_text or "")
        self.append_log(table)
        if not self._setup_mode:
            self.append_log(Text('Example: /update "My Playlist" --count 10 --fresh-days 21', style="dim"))

    def _command_summaries(self) -> Iterable[Tuple[str, str]]:
        for action in self.parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for choice in action._choices_actions:
                    name = choice.dest
                    if name == "interactive":
                        continue
                    yield name, choice.help or ""

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
            command_input.placeholder = "Setup required. Type /setup"
        else:
            command_input.placeholder = "Type /help for commands"

    def _render_top_bar(self):
        width = self.size.width or 0
        label_text = "tunr"
        content_width = max(0, width - 4)
        max_status_width = max(0, content_width - len(label_text) - 1)
        status_style = "green" if self.status == "Idle" else "yellow"
        if self._setup_mode:
            status_style = "red"
        if max_status_width < 8:
            return Text(label_text, style="bold cyan")
        status_text = Text(self.status, style=status_style)
        status_text.truncate(max_status_width, overflow="ellipsis")
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")
        table.add_row(Text(label_text, style="bold cyan"), status_text)
        return table

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
        if self._setup_mode and self.status == "Idle":
            self.status = "Setup Required"
        elif not self._setup_mode and self.status == "Setup Required":
            self.status = "Idle"
        if prev_setup != self._setup_mode:
            self._update_setup_screen()
            self._update_top_bar()
            if not self._setup_mode:
                self.append_log(Text("Setup complete. Type /help to continue.", style="green"))

    def _env_table(self) -> Table:
        table = Table(title="Environment Keys", box=box.SIMPLE, show_header=True, header_style="bold", expand=True)
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
        providers = []
        if self._env_status.get("ANTHROPIC_API_KEY"):
            providers.append("claude")
        if self._env_status.get("OPENAI_API_KEY"):
            providers.append("codex")
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
        setup.append("3) Optional: set ANTHROPIC_API_KEY and/or OPENAI_API_KEY for /search\n", style="dim")
        providers = []
        if self._env_status.get("ANTHROPIC_API_KEY"):
            providers.append("claude")
        if self._env_status.get("OPENAI_API_KEY"):
            providers.append("codex")
        provider_text = Text(
            f"Deep search providers: {', '.join(providers) if providers else 'none detected'}",
            style="dim",
        )
        return Group(Panel(setup, title="Setup", border_style="cyan"), self._env_table(), provider_text)

    def _prompt_search_followup(self) -> None:
        self._pending_action = "search_confirm"
        self._pending_payload = {
            "results": self.cli.last_search_results,
            "query": self.cli.last_search_query,
        }
        self.append_log(Text("Add these recommendations to the database and/or create a playlist? (yes/no)", style="bold"))
        self.append_log(Text("Or run /expand to broaden the search.", style="dim"))

    def _handle_pending_input(self, raw: str) -> None:
        value = raw.strip().lower()
        if self._pending_action == "search_confirm":
            if value in {"yes", "y"}:
                self._pending_action = "search_action"
                self.append_log(Text("Choose: db, playlist, both, or cancel", style="bold"))
                return
            if value in {"no", "n"}:
                self.append_log(Text("No problem. Try /search <criteria> or /expand to broaden.", style="dim"))
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
                self.append_log(Text("Cancelled. Try /search <criteria> to run again.", style="dim"))
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

    def _apply_search_results(self, mode: str, playlist_name: Optional[str] = None) -> None:
        if self.status != "Idle":
            self.append_log(Text("Another command is already running.", style="yellow"))
            return
        results = self._pending_payload.get("results") or []
        if not results:
            self.append_log(Text("No search results available.", style="yellow"))
            self._clear_pending()
            return

        def _worker() -> None:
            try:
                songs_for_playlist = []
                if mode in {"db", "both"}:
                    songs_for_playlist = self.cli.add_search_results_to_db(results)
                if mode in {"playlist", "both"}:
                    if not playlist_name:
                        return
                    if not songs_for_playlist:
                        songs_for_playlist = self.cli.resolve_search_results_for_playlist(results)
                    self.cli.create_playlist_from_search_results(playlist_name, songs_for_playlist)
            finally:
                self.call_from_thread(self._set_idle)

        self.status = "Applying search results"
        self.run_worker(_worker, thread=True)
        self._clear_pending()

    def _expand_search(self) -> None:
        if self._setup_mode:
            self.append_log(Text("Finish setup before running searches.", style="yellow"))
            return
        if not self.cli.last_search_query:
            self.append_log(Text("No previous search to expand. Run /search <criteria> first.", style="yellow"))
            return
        if self.status != "Idle":
            self.append_log(Text("Another command is already running.", style="yellow"))
            return
        self.append_log(Text(f"Expanding search: {self.cli.last_search_query}", style="bold"))
        self.status = "Running /expand"
        self.run_worker(lambda: self._execute_expand(), thread=True)

    def _execute_expand(self) -> None:
        try:
            self.cli.search_songs(self.cli.last_search_query, expanded=True)
        finally:
            self.call_from_thread(self._set_idle)

    def _clear_pending(self) -> None:
        self._pending_action = None
        self._pending_payload = {}


def run_interactive() -> int:
    parser = setup_parsers()
    cli = PlaylistCLI()
    app = PlaylistInteractiveApp(cli=cli, parser=parser)
    app.run()
    return 0
