"""
Unit tests for interactive app command routing and handling.
Tests all interactive-only commands, aliases, shlex error handling,
setup mode gating, and worker error display.
"""

import re

import pytest

from arg_parse import setup_parsers
from interactive_app import (
    COMMANDS_ALLOWED_WITHOUT_SPOTIFY,
    SPOTIFY_REQUIRED_KEYS,
    PlaylistInteractiveApp,
)
from main import PlaylistCLI
from ui import ACCENT_BLUE


@pytest.fixture(autouse=True)
def _isolated_history(monkeypatch, tmp_path):
    """Every app __init__ resolves the history path; keep tests off real data/."""
    monkeypatch.setenv("TUNR_HISTORY_PATH", str(tmp_path / "tunr_history"))
    return tmp_path / "tunr_history"


class DummyApp(PlaylistInteractiveApp):
    """Minimal subclass that captures log output and command dispatch without Textual UI."""

    def __init__(self, cli, parser):
        super().__init__(cli=cli, parser=parser)
        self.logged = []
        self.commands = []
        self._quit_called = False
        self._clear_called = False

    def append_log(self, renderable) -> None:
        self.logged.append(renderable)

    def _run_command(self, command: str, args: object) -> None:
        self.commands.append(command)

    def action_quit(self) -> None:
        self._quit_called = True

    def action_clear_log(self) -> None:
        self._clear_called = True


def _make_app(monkeypatch, with_spotify=True):
    """Helper to create a DummyApp with optional Spotify keys."""
    if with_spotify:
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
    else:
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.delenv(key, raising=False)
    app = DummyApp(cli=PlaylistCLI(), parser=setup_parsers())
    app._refresh_env_status()
    return app


# ============================================================================
# Compose: output log configuration
# ============================================================================


def _compose_widgets(app):
    """Iterate app.compose() without a running app (offline pattern).

    Bare list(app.compose()) raises NoActiveAppError because of the
    `with Container(...)` block, so temporarily install the app as the
    active app and give it a compose stack.
    """
    import textual._context as _ctx

    token = _ctx.active_app.set(app)
    app._compose_stacks.append([])
    app._composed.append([])
    try:
        return list(app.compose())
    finally:
        app._compose_stacks.pop()
        app._composed.pop()
        _ctx.active_app.reset(token)


class TestComposeOutputLog:
    def test_output_richlog_wraps_long_lines(self, monkeypatch):
        app = _make_app(monkeypatch)
        widgets = _compose_widgets(app)
        output = next(w for w in widgets if getattr(w, "id", None) == "output")
        assert output.wrap is True
        assert output.min_width == 20


# ============================================================================
# Interactive-only command routing
# ============================================================================


def _logged_text(app, width: int = 100) -> str:
    """Render every logged renderable to plain text for content assertions."""
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    console = Console(file=buf, width=width)
    for renderable in app.logged:
        console.print(renderable)
    return buf.getvalue()


class TestHelpCommand:
    def test_help_routed(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help")
        assert app.logged  # help output was logged
        assert app.commands == []  # not dispatched to argparse

    def test_question_mark_alias(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/?")
        assert app.logged
        assert app.commands == []

    def test_help_is_grouped_by_task(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help")
        text = _logged_text(app)
        # Task groups render as titled sections...
        assert "Set up" in text
        assert "Playlists" in text
        assert "Insight" in text
        # ...and commands land in them (e.g. the new /profile under Insight).
        assert "/profile" in text
        # /undo is surfaced under Playlists.
        assert "/undo" in text
        # /enrich and /sonic are surfaced under Set up.
        assert "/enrich" in text
        assert "/sonic" in text
        # /import-history sits in Set up next to its sibling /listen-sync.
        assert "/import-history" in text
        # /find (the flagship) is surfaced under Discover.
        assert "/find" in text

    def test_help_hides_legacy_by_default(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help")
        text = _logged_text(app)
        # Exact-row match: /import (legacy) is hidden; /import-history is a
        # different, non-legacy command and may legitimately appear.
        assert not re.search(r"/import(?![-\w])", text)  # legacy hidden
        assert "/help all" in text  # but discoverable

    def test_help_all_reveals_legacy(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help all")
        text = _logged_text(app)
        assert "Legacy" in text
        assert re.search(r"/import(?![-\w])", text)  # the legacy row itself
        assert app.commands == []  # still not dispatched to argparse


class TestCommandHelp:
    def test_help_subcommand_shows_cyan_panel_with_flags(self, monkeypatch):
        from rich.panel import Panel

        app = _make_app(monkeypatch)
        app._handle_command("/help update")
        assert app.commands == []
        panel = app.logged[-1]
        assert isinstance(panel, Panel)
        assert panel.title == "/update"
        assert panel.border_style == ACCENT_BLUE
        assert "--count" in _logged_text(app)

    def test_help_subcommand_accepts_slash_prefix(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help /update")
        assert "--count" in _logged_text(app)

    def test_update_help_flag_renders_help_not_error(self, monkeypatch):
        from rich.panel import Panel

        app = _make_app(monkeypatch)
        app._handle_command("/update --help")
        assert app.commands == []
        panel = app.logged[-1]
        assert isinstance(panel, Panel)
        assert panel.title == "Help"
        assert panel.border_style == ACCENT_BLUE
        assert "--count" in _logged_text(app)

    def test_missing_arg_error_carries_usage(self, monkeypatch):
        from rich.panel import Panel

        app = _make_app(monkeypatch)
        app._handle_command("/update")
        panel = app.logged[-1]
        assert isinstance(panel, Panel)
        assert panel.title == "Error"
        text = _logged_text(app)
        assert "usage:" in text
        assert "playlist" in text

    def test_typo_gets_did_you_mean(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/serch indie")
        assert app.commands == []
        text = _logged_text(app)
        assert "/search" in text
        assert "Did you mean" in text

    def test_help_debug_shows_meta_usage(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help debug")
        assert "Usage: /debug [errors|last|track <id>]" in _logged_text(app)

    def test_help_meta_command(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help clear")
        assert "Clear the output pane" in _logged_text(app)

    def test_help_unknown_name_gets_did_you_mean(self, monkeypatch):
        from rich.panel import Panel

        app = _make_app(monkeypatch)
        app._handle_command("/help serch")
        panel = app.logged[-1]
        assert isinstance(panel, Panel)
        assert panel.title == "Error"
        assert "/search" in _logged_text(app)


class TestSetupCommand:
    def test_setup_routed(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/setup")
        assert app.logged
        assert app.commands == []


class TestEnvCommand:
    def test_env_routed(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/env")
        assert app.logged
        assert app.commands == []

    def test_keys_alias(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/keys")
        assert app.logged
        assert app.commands == []


class TestDebugCommand:
    def test_debug_bare_shows_errors(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/debug")
        assert app.logged
        assert app.commands == []

    def test_debug_errors_subcommand(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/debug errors")
        assert app.logged
        assert app.commands == []

    def test_debug_last_subcommand(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.last_search_query = None  # no previous search
        app._handle_command("/debug last")
        assert app.logged
        assert app.commands == []

    def test_debug_track_no_id(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/debug track")
        assert app.logged  # Should show usage message
        assert app.commands == []

    def test_debug_invalid_subcommand(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/debug foo")
        assert app.logged  # Should show usage message
        assert app.commands == []


class TestErrorsCommand:
    def test_errors_routed(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/errors")
        assert app.logged
        assert app.commands == []


class TestExpandCommand:
    def test_expand_routed(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.last_search_query = None
        app._handle_command("/expand")
        assert app.logged  # shows "No previous search" message
        assert app.commands == []

    def test_search_more_alias(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.last_search_query = None
        app._handle_command("/search-more")
        assert app.logged
        assert app.commands == []


class TestClearCommand:
    def test_clear_routed(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/clear")
        assert app._clear_called
        assert app.commands == []

    def test_cls_alias(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/cls")
        assert app._clear_called
        assert app.commands == []


class TestQuitCommand:
    def test_quit_routed(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/quit")
        assert app._quit_called
        assert app.commands == []

    def test_exit_alias(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/exit")
        assert app._quit_called
        assert app.commands == []


# ============================================================================
# Argparse-based command routing through interactive
# ============================================================================


class TestArgparseCommandRouting:
    def test_stats_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/stats")
        assert "stats" in app.commands

    def test_backup_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/backup")
        assert "backup" in app.commands

    def test_list_backups_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/list-backups")
        assert "list-backups" in app.commands

    def test_search_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/search indie rock")
        assert "search" in app.commands

    def test_update_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command('/update "My Playlist"')
        assert "update" in app.commands

    def test_view_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command('/view "Test Playlist"')
        assert "view" in app.commands

    def test_sync_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command('/sync "Test Playlist"')
        assert "sync" in app.commands

    def test_clean_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/clean --dry-run")
        assert "clean" in app.commands

    def test_auth_status_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/auth-status")
        assert "auth-status" in app.commands

    def test_auth_refresh_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/auth-refresh")
        assert "auth-refresh" in app.commands

    def test_ingest_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/ingest liked")
        assert "ingest" in app.commands

    def test_listen_sync_dispatched(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/listen-sync")
        assert "listen-sync" in app.commands

    def test_debug_cli_dispatched(self, monkeypatch):
        """CLI /debug (with topic) goes through argparse, not interactive handler."""
        # Note: bare /debug goes to interactive handler (errors display),
        # but /debug last with explicit topic routes through argparse
        # Actually, /debug with or without args always goes to interactive handler
        app = _make_app(monkeypatch)
        app._handle_command("/debug")
        # Bare /debug is handled interactively, not dispatched
        assert "debug" not in app.commands


# ============================================================================
# shlex.split() ValueError handling
# ============================================================================


class TestShlexErrorHandling:
    def test_unbalanced_single_quote(self, monkeypatch):
        """Unbalanced single quote should show error, not crash."""
        app = _make_app(monkeypatch)
        app._handle_command("/update 'My Playlist")
        assert app.commands == []
        assert app.logged  # error panel was shown

    def test_unbalanced_double_quote(self, monkeypatch):
        """Unbalanced double quote should show error, not crash."""
        app = _make_app(monkeypatch)
        app._handle_command('/search "incomplete query')
        assert app.commands == []
        assert app.logged  # error panel was shown

    def test_valid_quoted_args(self, monkeypatch):
        """Properly quoted args should work fine."""
        app = _make_app(monkeypatch)
        app._handle_command('/update "My Playlist" --count 10')
        assert "update" in app.commands


# ============================================================================
# Interactive command handler
# ============================================================================


class TestInteractiveCommandInInteractive:
    def test_interactive_command_shows_message(self, monkeypatch):
        """/interactive when already in interactive mode should show a message, not dispatch."""
        app = _make_app(monkeypatch)
        app._handle_command("/interactive")
        assert app.commands == []
        assert app.logged  # "Already in interactive mode" message


# ============================================================================
# Setup mode gating
# ============================================================================


class TestSetupModeGating:
    def test_setup_mode_blocks_spotify_commands(self, monkeypatch):
        """When Spotify keys are missing, commands requiring Spotify should be blocked."""
        app = _make_app(monkeypatch, with_spotify=False)

        app._handle_command('/update "My Playlist"')

        assert app.commands == []
        assert app.logged  # setup required warning

    def test_setup_mode_allows_backup(self, monkeypatch):
        """Backup should work even without Spotify keys."""
        app = _make_app(monkeypatch, with_spotify=False)

        app._handle_command("/backup")

        assert app.commands == ["backup"]

    def test_setup_mode_allows_list_backups(self, monkeypatch):
        app = _make_app(monkeypatch, with_spotify=False)
        app._handle_command("/list-backups")
        assert "list-backups" in app.commands

    def test_setup_mode_allows_stats(self, monkeypatch):
        app = _make_app(monkeypatch, with_spotify=False)
        app._handle_command("/stats")
        assert "stats" in app.commands

    def test_setup_mode_allows_search(self, monkeypatch):
        app = _make_app(monkeypatch, with_spotify=False)
        app._handle_command("/search jazz")
        assert "search" in app.commands

    def test_setup_mode_allows_plan(self, monkeypatch):
        app = _make_app(monkeypatch, with_spotify=False)
        app._handle_command('/plan "Test"')
        assert "plan" in app.commands

    def test_setup_mode_blocks_view(self, monkeypatch):
        app = _make_app(monkeypatch, with_spotify=False)
        app._handle_command('/view "Test"')
        assert app.commands == []

    def test_setup_mode_blocks_sync(self, monkeypatch):
        app = _make_app(monkeypatch, with_spotify=False)
        app._handle_command('/sync "Test"')
        assert app.commands == []

    def test_setup_mode_blocks_rotate(self, monkeypatch):
        app = _make_app(monkeypatch, with_spotify=False)
        app._handle_command('/rotate "Test"')
        assert app.commands == []

    def test_setup_mode_allows_all_whitelisted(self, monkeypatch):
        """Every command in COMMANDS_ALLOWED_WITHOUT_SPOTIFY should be allowed."""
        for cmd in COMMANDS_ALLOWED_WITHOUT_SPOTIFY:
            app = _make_app(monkeypatch, with_spotify=False)
            # Some commands need args, provide minimal ones
            if cmd in ("list-rotations",):
                app._handle_command(f'/{cmd} "Test"')
            elif cmd in ("restore",):
                app._handle_command(f"/{cmd} backup_name")
            elif cmd in ("search", "find"):
                app._handle_command(f"/{cmd} jazz")
            elif cmd in ("plan",):
                app._handle_command(f'/{cmd} "Test"')
            elif cmd in ("import-history",):
                # Local-only GDPR import: must work before API keys exist.
                app._handle_command(f"/{cmd} export.zip")
            elif cmd in ("interactive",):
                # /interactive is handled before setup check
                continue
            else:
                app._handle_command(f"/{cmd}")
            assert cmd in app.commands, f"/{cmd} should be allowed without Spotify keys"


# ============================================================================
# Empty / whitespace input
# ============================================================================


class TestEmptyInput:
    def test_empty_string_ignored(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("")
        assert app.commands == []
        assert app.logged == []

    def test_slash_only_ignored(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/")
        assert app.commands == []

    def test_whitespace_only_ignored(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("   ")
        assert app.commands == []


# ============================================================================
# Parse error display
# ============================================================================


class TestParseErrorDisplay:
    def test_invalid_command_shows_error(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/nonexistent-command")
        assert app.commands == []
        assert app.logged  # error panel was shown

    def test_missing_required_arg_shows_error(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/update")  # missing playlist argument
        assert app.commands == []
        assert app.logged  # error panel was shown


# ============================================================================
# Search follow-up prompt (the modal wizard) — fires for a plain /search but is
# suppressed once --to/--save already handled the results.
# ============================================================================


# ============================================================================
# Thread-safe UI marshalling, rc surfacing, toasts, preview dismissal
# ============================================================================


class WorkerApp(DummyApp):
    """DummyApp variant for exercising _execute_command off the worker seam.

    call_from_thread invokes directly (tests are single-threaded) and notify
    captures toasts instead of needing a running app.
    """

    def __init__(self, cli, parser):
        super().__init__(cli=cli, parser=parser)
        self.notifications = []
        self.marshalled = []

    def call_from_thread(self, fn, *args, **kwargs):
        self.marshalled.append(fn)
        return fn(*args, **kwargs)

    def notify(self, message, *, title="", severity="information", timeout=None, markup=True):
        self.notifications.append((message, severity))


class TestDispatchUI:
    def test_direct_call_on_app_thread(self, monkeypatch):
        """When already on the app thread, _dispatch_ui must NOT marshal."""
        import threading

        app = _make_app(monkeypatch)
        app._app_thread_id = threading.get_ident()

        def _boom(*args, **kwargs):
            raise AssertionError("call_from_thread used from the app thread")

        app.call_from_thread = _boom
        calls = []
        app._dispatch_ui(calls.append, "rendered")
        assert calls == ["rendered"]

    def test_marshals_from_other_thread(self, monkeypatch):
        import threading

        app = _make_app(monkeypatch)
        app._app_thread_id = threading.get_ident() + 1  # pretend we're a worker
        marshalled = []
        app.call_from_thread = lambda fn, *args: marshalled.append((fn, args))
        calls = []
        app._dispatch_ui(calls.append, "rendered")
        assert calls == []
        assert marshalled == [(calls.append, ("rendered",))]

    def test_direct_call_before_mount(self, monkeypatch):
        """Unmounted apps (no captured thread id) fall back to a direct call."""
        app = _make_app(monkeypatch)
        assert app._app_thread_id is None
        calls = []
        app._dispatch_ui(calls.append, "rendered")
        assert calls == ["rendered"]

    def test_emit_renderable_uses_dispatch_ui(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._emit_renderable("hello")  # _app_thread_id None -> direct append_log
        assert "hello" in app.logged


class TestUILogHandlerDispatch:
    def test_ui_thread_record_is_not_dropped(self, monkeypatch):
        """Log records emitted from the app thread must reach the log."""
        import logging as _logging
        import threading

        from interactive_app import UILogHandler

        app = _make_app(monkeypatch)
        app._app_thread_id = threading.get_ident()
        app.call_from_thread = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("from app thread")
        )
        handler = UILogHandler(app)
        record = _logging.LogRecord("test", _logging.WARNING, __file__, 1, "boom", None, None)
        handler.emit(record)
        assert any("boom" in str(entry) for entry in app.logged)
        assert any("boom" in entry for entry in app._error_log)


class TestRcSurfacing:
    def _worker_app(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = WorkerApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        app.cli.last_search_results = None
        return app

    def test_nonzero_rc_logs_red_line_and_error_toast(self, monkeypatch):
        import interactive_app as ia

        app = self._worker_app(monkeypatch)
        monkeypatch.setattr(ia, "dispatch_command", lambda cli, cmd, args: 1)
        app._execute_command("stats", object())
        text = _logged_text(app)
        assert "/stats exited with errors" in text
        assert "/debug errors" in text
        assert ("/stats exited with errors", "error") in app.notifications
        assert len(app.notifications) == 1

    def test_exception_logs_panel_and_error_toast(self, monkeypatch):
        import interactive_app as ia

        app = self._worker_app(monkeypatch)

        def _raise(cli, cmd, args):
            raise RuntimeError("kaput")

        monkeypatch.setattr(ia, "dispatch_command", _raise)
        app._execute_command("stats", object())
        text = _logged_text(app)
        assert "Command /stats failed: kaput" in text
        assert ("/stats exited with errors", "error") in app.notifications
        assert len(app.notifications) == 1

    def test_zero_rc_fast_no_toast_no_red_line(self, monkeypatch):
        import time as _time

        import interactive_app as ia

        app = self._worker_app(monkeypatch)
        monkeypatch.setattr(ia, "dispatch_command", lambda cli, cmd, args: 0)
        app._run_started = _time.monotonic()
        app._execute_command("stats", object())
        assert app.notifications == []
        assert "exited with errors" not in _logged_text(app)

    def test_zero_rc_slow_information_toast(self, monkeypatch):
        import time as _time

        import interactive_app as ia

        app = self._worker_app(monkeypatch)
        monkeypatch.setattr(ia, "dispatch_command", lambda cli, cmd, args: 0)
        app._run_started = _time.monotonic() - 11
        app._execute_command("stats", object())
        assert app.notifications == [("/stats finished in 11s", "information")]


class TestPostCommandPreviewDismissal:
    def test_post_command_clears_preview(self, monkeypatch):
        import ui

        app = _make_app(monkeypatch)
        app.cli.last_search_results = None
        captured = []
        ui.set_preview_sink(captured.append)
        try:
            app._post_command("search")
        finally:
            ui.set_preview_sink(None)
        assert captured == [None]


# ============================================================================
# Busy status truthfulness: spinner gate, elapsed formatting, last-run note
# ============================================================================


class SpinnerRecordingApp(DummyApp):
    """DummyApp that records spinner start/stop instead of touching timers."""

    def __init__(self, cli, parser):
        super().__init__(cli=cli, parser=parser)
        self.spinner_calls = []

    def _start_spinner(self) -> None:
        self.spinner_calls.append("start")

    def _stop_spinner(self) -> None:
        self.spinner_calls.append("stop")


class TestFormatElapsed:
    def test_seconds(self):
        assert PlaylistInteractiveApp._format_elapsed(4.7) == "4s"

    def test_zero_and_negative_clamped(self):
        assert PlaylistInteractiveApp._format_elapsed(0) == "0s"
        assert PlaylistInteractiveApp._format_elapsed(-3) == "0s"

    def test_minutes(self):
        assert PlaylistInteractiveApp._format_elapsed(125) == "2m05s"

    def test_hours(self):
        assert PlaylistInteractiveApp._format_elapsed(3720) == "1h02m"


class TestSpinnerGate:
    def _spinner_app(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = SpinnerRecordingApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        return app

    def test_applying_search_results_animates(self, monkeypatch):
        """Non-'running …' busy statuses must still animate the spinner."""
        app = self._spinner_app(monkeypatch)
        app.spinner_calls.clear()
        app.status = "applying search results"
        assert app.spinner_calls == ["start"]

    def test_idle_stops_spinner(self, monkeypatch):
        app = self._spinner_app(monkeypatch)
        app.status = "running /stats"
        app.spinner_calls.clear()
        app.status = "idle"
        assert app.spinner_calls == ["stop"]

    def test_setup_required_does_not_animate(self, monkeypatch):
        app = self._spinner_app(monkeypatch)
        app.spinner_calls.clear()
        app.status = "setup required"
        assert "start" not in app.spinner_calls


class TestCommandDuration:
    def test_post_command_logs_finished_line_and_sets_note(self, monkeypatch):
        import time as _time

        app = _make_app(monkeypatch)
        app.cli.last_search_results = None
        app._run_started = _time.monotonic() - 4.2
        app._post_command("stats")
        text = _logged_text(app)
        assert "/stats finished in 4s" in text
        assert app._last_run_note == "last: /stats 4s"
        assert app._run_started is None  # cleared by _set_idle

    def test_post_command_without_start_logs_nothing(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.last_search_results = None
        app._run_started = None
        app._post_command("stats")
        assert "finished in" not in _logged_text(app)
        assert app._last_run_note == ""


class _SubmitEvent:
    """Stub for Input.Submitted: .value plus an .input with a settable .value."""

    def __init__(self, value):
        from types import SimpleNamespace

        self.value = value
        self.input = SimpleNamespace(value=value)


class TestWizardSlashDismissal:
    def _armed_app(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.last_search_track_ids = ["artist|||song"]
        app.cli.last_search_query = "indie"
        app._prompt_search_followup()
        assert app._pending_action == "search_confirm"
        return app

    def test_slash_command_dismisses_pending_wizard(self, monkeypatch):
        app = self._armed_app(monkeypatch)
        app.on_input_submitted(_SubmitEvent("/stats"))
        assert app._pending_action is None
        assert "stats" in app.commands
        assert "Search follow-up dismissed." in _logged_text(app)

    def test_text_after_slash_dismissal_not_consumed_as_wizard_answer(self, monkeypatch):
        app = self._armed_app(monkeypatch)
        app.on_input_submitted(_SubmitEvent("/stats"))
        app.logged.clear()
        app.on_input_submitted(_SubmitEvent("yes"))
        # "yes" is treated as a normal (invalid) command, not a wizard answer.
        assert app._pending_action is None
        assert "Choose: db, playlist" not in _logged_text(app)

    def test_non_slash_text_still_routes_to_wizard(self, monkeypatch):
        app = self._armed_app(monkeypatch)
        app.on_input_submitted(_SubmitEvent("yes"))
        assert app._pending_action == "search_action"
        assert "Choose: db, playlist" in _logged_text(app)


class TestSearchFollowupSuppression:
    def test_wizard_fires_for_plain_search(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.last_search_results = [{"song": "X"}]
        app.cli.last_search_handled = False
        app._post_command("search")
        assert app._pending_action == "search_confirm"

    def test_wizard_suppressed_when_flags_handled(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.last_search_results = [{"song": "X"}]
        app.cli.last_search_handled = True  # /search --to ... already added them
        app._post_command("search")
        assert app._pending_action is None

    def test_no_wizard_without_results(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.last_search_results = None
        app.cli.last_search_handled = False
        app._post_command("search")
        assert app._pending_action is None


# ============================================================================
# Command history: persistence, dedupe, prefix-filtered recall
# ============================================================================


class HistoryApp(DummyApp):
    """DummyApp with the two thin Input seams overridden for history tests."""

    def __init__(self, cli, parser):
        super().__init__(cli=cli, parser=parser)
        self.input_value = ""

    def _write_input(self, value: str) -> None:
        self.input_value = value

    def _get_input_value(self) -> str:
        return self.input_value


def _make_history_app(monkeypatch, entries=()):
    for key in SPOTIFY_REQUIRED_KEYS:
        monkeypatch.setenv(key, "test_value")
    app = HistoryApp(cli=PlaylistCLI(), parser=setup_parsers())
    app._refresh_env_status()
    app._history = list(entries)
    return app


class TestHistoryWraparound:
    """Pin the pre-existing navigation semantics (untested before)."""

    def test_prev_at_oldest_stays(self, monkeypatch):
        app = _make_history_app(monkeypatch, ["a", "b"])
        app._history_prev()
        assert (app._history_index, app.input_value) == (1, "b")
        app._history_prev()
        assert (app._history_index, app.input_value) == (0, "a")
        app._history_prev()  # already at the oldest entry: stays put
        assert (app._history_index, app.input_value) == (0, "a")

    def test_next_past_end_clears(self, monkeypatch):
        app = _make_history_app(monkeypatch, ["a", "b"])
        app._history_prev()
        app._history_next()
        assert app._history_index is None
        assert app.input_value == ""

    def test_next_without_navigation_is_noop(self, monkeypatch):
        app = _make_history_app(monkeypatch, ["a"])
        app._history_next()
        assert app._history_index is None
        assert app.input_value == ""

    def test_prev_with_empty_history_is_noop(self, monkeypatch):
        app = _make_history_app(monkeypatch, [])
        app._history_prev()
        assert app._history_index is None


class TestHistoryPersistence:
    def test_round_trip_across_sessions(self, monkeypatch, _isolated_history):
        app = _make_app(monkeypatch)
        app.on_input_submitted(_SubmitEvent("/stats"))
        app.on_input_submitted(_SubmitEvent("/env"))
        assert _isolated_history.read_text(encoding="utf-8") == "/stats\n/env\n"
        reborn = _make_app(monkeypatch)
        assert reborn._history == ["/stats", "/env"]

    def test_missing_file_starts_empty(self, monkeypatch, _isolated_history):
        assert not _isolated_history.exists()
        app = _make_app(monkeypatch)
        assert app._history == []

    def test_corrupt_file_tolerated(self, monkeypatch, _isolated_history):
        _isolated_history.write_bytes(b"\xff\xfe\x80 not utf8 \x00garbage")
        app = _make_app(monkeypatch)
        assert app._history == []

    def test_load_caps_and_truncates_file(self, monkeypatch, _isolated_history):
        from interactive_app import HISTORY_MAX_LINES

        lines = [f"/cmd{i}" for i in range(HISTORY_MAX_LINES + 100)]
        _isolated_history.write_text("\n".join(lines) + "\n", encoding="utf-8")
        app = _make_app(monkeypatch)
        assert len(app._history) == HISTORY_MAX_LINES
        assert app._history[0] == "/cmd100"
        assert app._history[-1] == f"/cmd{HISTORY_MAX_LINES + 99}"
        on_disk = _isolated_history.read_text(encoding="utf-8").splitlines()
        assert len(on_disk) == HISTORY_MAX_LINES

    def test_consecutive_duplicates_skipped(self, monkeypatch, _isolated_history):
        app = _make_app(monkeypatch)
        app.on_input_submitted(_SubmitEvent("/stats"))
        app.on_input_submitted(_SubmitEvent("/stats"))
        app.on_input_submitted(_SubmitEvent("/env"))
        app.on_input_submitted(_SubmitEvent("/stats"))  # non-consecutive dup kept
        assert app._history == ["/stats", "/env", "/stats"]
        assert _isolated_history.read_text(encoding="utf-8") == "/stats\n/env\n/stats\n"

    def test_wizard_answers_not_persisted(self, monkeypatch, _isolated_history):
        app = _make_app(monkeypatch)
        app.cli.last_search_track_ids = ["artist|||song"]
        app.cli.last_search_query = "indie"
        app._prompt_search_followup()
        app.on_input_submitted(_SubmitEvent("no"))
        assert app._history == []
        assert not _isolated_history.exists()


class TestHistoryPrefixRecall:
    ENTRIES = ["/search indie", "/stats", "/search jazz", "/env"]

    def test_prefix_walk_backward_and_forward(self, monkeypatch):
        app = _make_history_app(monkeypatch, self.ENTRIES)
        app.input_value = "/se"
        app._history_prev()
        assert app.input_value == "/search jazz"
        app._history_prev()
        assert app.input_value == "/search indie"
        app._history_prev()  # oldest match: stays
        assert app.input_value == "/search indie"
        app._history_next()
        assert app.input_value == "/search jazz"
        app._history_next()  # no newer match -> exits navigation
        assert app._history_index is None
        assert app.input_value == ""

    def test_empty_input_recalls_newest(self, monkeypatch):
        app = _make_history_app(monkeypatch, self.ENTRIES)
        app.input_value = ""
        app._history_prev()
        assert app.input_value == "/env"

    def test_prefix_resets_after_navigation_exits(self, monkeypatch):
        app = _make_history_app(monkeypatch, self.ENTRIES)
        app.input_value = "/se"
        app._history_prev()
        app._history_next()  # exits navigation, resets the prefix
        app._history_prev()  # fresh start from the (nav-placed) empty input
        assert app.input_value == "/env"

    def test_nav_placed_value_is_not_a_prefix(self, monkeypatch):
        app = _make_history_app(monkeypatch, ["/search a", "/env"])
        app._navigating = True
        app._nav_placed_value = "/env"
        app.input_value = "/env"
        app._history_prev()
        assert app.input_value == "/env"
        app._history_prev()  # an "/env" prefix would stick; nav-placed must not
        assert app.input_value == "/search a"

    def test_no_match_leaves_input_untouched(self, monkeypatch):
        app = _make_history_app(monkeypatch, self.ENTRIES)
        app.input_value = "/zzz"
        app._history_prev()
        assert app._history_index is None
        assert app.input_value == "/zzz"


# ============================================================================
# Integrator regression tests (post-review fixes)
# ============================================================================


class TestPostCommandPreviewPersist:
    def test_preview_kept_when_search_flagged_persist(self, monkeypatch):
        """SEARCH_FINAL_TABLE_MODE=none leaves the preview as the ONLY copy of
        the results; _post_command must not dismiss it."""
        import ui

        app = _make_app(monkeypatch)
        app.cli.last_search_results = None
        app.cli.last_search_preview_persist = True
        captured = []
        ui.set_preview_sink(captured.append)
        try:
            app._post_command("search")
        finally:
            ui.set_preview_sink(None)
        assert captured == []  # no clear_preview(None) emission


class TestHistoryNavResetOnSubmit:
    def test_submit_ends_navigation_session(self, monkeypatch):
        app = _make_history_app(monkeypatch, ["/search indie", "/stats"])
        app._history_prev()  # places "/stats"
        assert app._navigating is True
        app.on_input_submitted(_SubmitEvent(app.input_value))
        assert app._navigating is False
        assert app._nav_placed_value is None
        assert app._history_prefix == ""

    def test_typed_text_equal_to_old_nav_value_still_prefix_filters(self, monkeypatch):
        """Regression: recall "/env" via Up and submit it; later TYPE "/env"
        manually and press Up twice. The stale nav marker used to disable the
        prefix filter, so the second Up fell through to "/search jazz"."""
        app = _make_history_app(monkeypatch, ["/search jazz", "/env"])
        app._history_prev()  # places "/env" (nav markers set)
        app.on_input_submitted(_SubmitEvent("/env"))
        app.input_value = "/env"  # typed manually this time
        app._history_prev()
        app._history_prev()  # must stay on /env-prefixed entries
        assert app.input_value == "/env"


class TestQuitExcludedFromHistory:
    """Persisting /quit makes the next session's first up-arrow recall it."""

    def test_quit_and_exit_not_recorded(self, monkeypatch):
        app = _make_history_app(monkeypatch, [])
        for raw in ("/quit", "/exit", "quit"):
            app._append_history(raw)
        assert app._history == []
        assert not app._history_path.exists() or app._history_path.read_text() == ""

    def test_other_commands_still_recorded(self, monkeypatch):
        app = _make_history_app(monkeypatch, [])
        app._append_history("/stats")
        assert app._history == ["/stats"]


# ---------------------------------------------------------------------------
# /results browser: open gating + the parametrized apply path
# ---------------------------------------------------------------------------


class TestOpenResults:
    """_open_results (the real method, push_screen stubbed for headless runs)."""

    def _app(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.pushed = []
        app.push_screen = lambda screen, callback=None: app.pushed.append((screen, callback))
        return app

    def test_no_cached_results_shows_message_not_empty_table(self, monkeypatch):
        app = self._app(monkeypatch)
        app.cli.last_search_results = None
        app.cli.last_find_ranked = None
        app._handle_command("/results")
        assert app.pushed == []
        assert "No cached results to browse" in _logged_text(app)

    def test_busy_status_blocks_open(self, monkeypatch):
        app = self._app(monkeypatch)
        app.cli.last_search_results = [{"song": "A", "artist": "B", "track_id": "b|||a"}]
        app.status = "running /stats"
        app._handle_command("/results")
        assert app.pushed == []
        assert "Another command is already running" in _logged_text(app)

    def test_cached_results_push_results_screen(self, monkeypatch):
        from results_screen import ResultsScreen

        app = self._app(monkeypatch)
        app.cli.last_search_results = [{"song": "A", "artist": "B", "track_id": "b|||a"}]
        app._handle_command("/browse")
        assert len(app.pushed) == 1
        assert isinstance(app.pushed[0][0], ResultsScreen)

    def test_dismiss_action_routes_subset_into_apply(self, monkeypatch):
        from results_screen import ResultsAction

        app = self._app(monkeypatch)
        app.cli.last_search_results = [
            {"song": "A", "artist": "B", "track_id": "b|||a"},
            {"song": "C", "artist": "D", "track_id": "d|||c"},
        ]
        applied = []
        app._apply_search_results = lambda mode, playlist_name=None, track_ids=None: applied.append(
            (mode, playlist_name, track_ids)
        )
        app._handle_command("/results")
        _screen, callback = app.pushed[0]
        callback(ResultsAction(mode="playlist", track_ids=["d|||c"], playlist_name="mix"))
        assert applied == [("playlist", "mix", ["d|||c"])]

    def test_dismiss_none_applies_nothing(self, monkeypatch):
        app = self._app(monkeypatch)
        app.cli.last_search_results = [{"song": "A", "artist": "B", "track_id": "b|||a"}]
        applied = []
        app._apply_search_results = lambda mode, playlist_name=None, track_ids=None: applied.append(
            mode
        )
        app._handle_command("/results")
        _screen, callback = app.pushed[0]
        callback(None)
        assert applied == []


class _ApplyWorkerApp(DummyApp):
    """Runs _apply_search_results' worker synchronously (headless)."""

    def run_worker(self, fn, thread=False):
        fn()

    def call_from_thread(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)


class _RecordingCli:
    def __init__(self):
        self.marked = []
        self.playlist_writes = []
        self.last_search_results = None
        self.last_find_ranked = None
        self.last_search_query = None

    def mark_search_tracks(self, track_ids, status="accepted"):
        self.marked.append((list(track_ids), status))

    def add_search_to_playlist(self, playlist_name, track_ids):
        self.playlist_writes.append((playlist_name, list(track_ids)))


class TestApplySearchResultsParametrized:
    """The refactor: explicit track_ids bypass the wizard's pending payload."""

    def _app(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = _ApplyWorkerApp(cli=_RecordingCli(), parser=setup_parsers())
        app._refresh_env_status()
        return app

    def test_explicit_track_ids_bypass_pending_payload(self, monkeypatch):
        app = self._app(monkeypatch)
        app._pending_payload = {"track_ids": ["wizard|||row"]}
        app._apply_search_results(mode="db", track_ids=["picked|||row"])
        assert app.cli.marked == [(["picked|||row"], "accepted")]
        assert app.status == "idle"  # worker completed and reset

    def test_wizard_path_still_uses_pending_payload(self, monkeypatch):
        app = self._app(monkeypatch)
        app._pending_payload = {"track_ids": ["wizard|||row"]}
        app._apply_search_results(mode="db")
        assert app.cli.marked == [(["wizard|||row"], "accepted")]

    def test_playlist_mode_routes_subset_to_playlist(self, monkeypatch):
        app = self._app(monkeypatch)
        app._apply_search_results(mode="playlist", playlist_name="mix", track_ids=["picked|||row"])
        assert app.cli.playlist_writes == [("mix", ["picked|||row"])]
        assert app.cli.marked == []

    def test_empty_explicit_ids_logs_message(self, monkeypatch):
        app = self._app(monkeypatch)
        app._apply_search_results(mode="db", track_ids=[])
        assert app.cli.marked == []
        assert "No search results available" in _logged_text(app)
