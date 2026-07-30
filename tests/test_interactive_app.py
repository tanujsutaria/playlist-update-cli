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
        # Pin the provider pre-flight open: this test is about ROUTING, and
        # must not depend on API keys present in the developer/CI env.
        monkeypatch.setattr("interactive_app.detect_search_commands", lambda: {"claude": "cmd"})
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
        # Providers pinned on: the subject here is the setup-mode gate, not
        # the provider pre-flight (covered by TestSearchProviderPreflight).
        monkeypatch.setattr("interactive_app.detect_search_commands", lambda: {"claude": "cmd"})
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
        # Providers pinned OFF: reproduces keyless GitHub CI (ci.yml passes no
        # API-key secrets), so this loop is provider-agnostic no matter what
        # the developer shell exports — the /search//find pre-flight is
        # advisory and must never block a whitelisted command.
        monkeypatch.setattr("interactive_app.detect_search_commands", lambda: {})
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


# ============================================================================
# Error-aware completion: ERROR-level records during an rc==0 command flip the
# completion line from the dim "finished" to the red "exited with errors".
# ============================================================================


class TestErrorAwareCompletion:
    def _worker_app(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = WorkerApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        app.cli.last_search_results = None
        return app

    def test_error_record_increments_counter_warning_does_not(self, monkeypatch):
        import logging as _logging

        from interactive_app import UILogHandler

        app = _make_app(monkeypatch)
        handler = UILogHandler(app)
        warn = _logging.LogRecord("t", _logging.WARNING, __file__, 1, "meh", None, None)
        err = _logging.LogRecord("t", _logging.ERROR, __file__, 1, "boom", None, None)
        handler.emit(warn)
        assert app._command_error_count == 0
        handler.emit(err)
        handler.emit(err)
        assert app._command_error_count == 2

    def test_run_command_resets_counter_and_stage(self, monkeypatch):
        app = self._worker_app(monkeypatch)
        app._command_error_count = 3
        app._stage = "stale stage"
        workers = []
        monkeypatch.setattr(
            app, "run_worker", lambda fn, thread=True: workers.append(fn), raising=False
        )
        PlaylistInteractiveApp._run_command(app, "stats", object())
        assert app._command_error_count == 0
        assert app._stage == ""
        assert app.status == "running /stats"
        assert len(workers) == 1

    def test_zero_rc_with_errors_red_line_toast_no_finished_line(self, monkeypatch):
        import time as _time

        import interactive_app as ia

        app = self._worker_app(monkeypatch)

        def _dispatch(cli, cmd, args):
            app._command_error_count += 1  # a logger.error fired mid-command
            return 0

        monkeypatch.setattr(ia, "dispatch_command", _dispatch)
        app._run_started = _time.monotonic()
        app._execute_command("stats", object())
        text = _logged_text(app)
        assert "/stats exited with errors (1 error logged)" in text
        assert "/debug errors" in text
        assert ("/stats exited with errors", "error") in app.notifications
        assert "finished in" not in text  # the dim success line is suppressed

    def test_zero_rc_with_multiple_errors_pluralizes(self, monkeypatch):
        import interactive_app as ia

        app = self._worker_app(monkeypatch)

        def _dispatch(cli, cmd, args):
            app._command_error_count += 2
            return 0

        monkeypatch.setattr(ia, "dispatch_command", _dispatch)
        app._execute_command("stats", object())
        assert "(2 errors logged)" in _logged_text(app)

    def test_nonzero_rc_with_errors_no_double_print(self, monkeypatch):
        import interactive_app as ia

        app = self._worker_app(monkeypatch)

        def _dispatch(cli, cmd, args):
            app._command_error_count += 2
            return 1

        monkeypatch.setattr(ia, "dispatch_command", _dispatch)
        app._execute_command("stats", object())
        text = _logged_text(app)
        assert text.count("exited with errors") == 1
        assert len(app.notifications) == 1


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

    def test_post_command_failed_suppresses_finished_line(self, monkeypatch):
        """failed=True skips the dim line (the red one already rendered) but
        still records the last-run note and returns to idle."""
        import time as _time

        app = _make_app(monkeypatch)
        app.cli.last_search_results = None
        app._run_started = _time.monotonic() - 4.2
        app._post_command("stats", failed=True)
        assert "finished in" not in _logged_text(app)
        assert app._last_run_note == "last: /stats 4s"
        assert app._run_started is None
        assert app.status == "idle"


# ============================================================================
# Live stage in the top bar: ui status sink -> _set_stage -> _render_top_bar
# ============================================================================


class SizedApp(DummyApp):
    """DummyApp with a fixed size so _render_top_bar has room to render."""

    @property
    def size(self):
        from textual.geometry import Size

        return Size(120, 40)


def _render_to_text(renderable, width: int = 120) -> str:
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    Console(file=buf, width=width).print(renderable)
    return buf.getvalue()


class TestStageInTopBar:
    def _sized_app(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = SizedApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        return app

    def test_emit_status_sets_stage_via_dispatch(self, monkeypatch):
        app = self._sized_app(monkeypatch)
        app._emit_status("extract 87/120")
        assert app._stage == "extract 87/120"
        app._emit_status(None)  # None clears
        assert app._stage == ""

    def test_stage_rendered_while_running(self, monkeypatch):
        import time as _time

        app = self._sized_app(monkeypatch)
        app.status = "running /search"
        app._run_started = _time.monotonic()
        app._set_stage("extract 87/120")
        text = _render_to_text(app._render_top_bar())
        assert "running /search · extract 87/120" in text

    def test_stage_hidden_when_idle(self, monkeypatch):
        app = self._sized_app(monkeypatch)
        app._stage = "extract 87/120"  # stale stage must never show at idle
        text = _render_to_text(app._render_top_bar())
        assert "extract 87/120" not in text

    def test_no_stage_renders_plain_running_label(self, monkeypatch):
        app = self._sized_app(monkeypatch)
        app.status = "running /search"
        text = _render_to_text(app._render_top_bar())
        assert "running /search" in text
        assert "·" not in text  # no stray separator without a stage

    def test_set_idle_clears_stage(self, monkeypatch):
        app = self._sized_app(monkeypatch)
        app.status = "running /search"
        app._set_stage("score 25/50")
        app._set_idle()
        assert app._stage == ""

    def test_status_sink_registered_and_cleared_with_app_lifecycle(self, monkeypatch):
        """on_mount installs ui's status sink; on_shutdown clears it (same
        lifecycle as the output/preview sinks)."""
        import ui

        app = self._sized_app(monkeypatch)
        # Simulate the shutdown half directly (mount needs a live Textual app).
        ui.set_status_sink(app._emit_status)
        try:
            ui.emit_status("providers 3/10")
            assert app._stage == "providers 3/10"
            app.on_shutdown()
            ui.emit_status("providers 4/10")
            assert app._stage == "providers 3/10"  # sink uninstalled: unchanged
        finally:
            ui.set_status_sink(None)


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


# ============================================================================
# Ghost-text autocomplete (suggester wiring + Pilot end-to-end)
# ============================================================================


class TestSuggesterWiring:
    def test_compose_input_carries_the_suggester(self, monkeypatch):
        from textual.widgets import Input

        from completions import TunrSuggester

        app = _make_app(monkeypatch)
        widgets = _compose_widgets(app)
        command_input = next(w for w in widgets if isinstance(w, Input))
        assert command_input.suggester is app._suggester
        assert isinstance(app._suggester, TunrSuggester)

    def test_inventory_covers_subcommands_meta_and_flags(self, monkeypatch):
        app = _make_app(monkeypatch)
        suggester = app._suggester
        assert "update" in suggester._commands
        assert "help" in suggester._commands  # meta command
        assert "interactive" not in suggester._commands  # hidden in the UI
        assert "--fresh-days" in suggester._flags["update"]
        assert "--playlist" in suggester._flags["stats"]

    def test_history_provider_follows_list_rebind(self, monkeypatch):
        """_append_history REBINDS self._history on truncation; the suggester
        must keep seeing the live list, not a stale captured reference."""
        app = _make_app(monkeypatch)
        app._history = ["/stats --json"]  # rebind, like the truncation path
        assert app._suggester._history() == ["/stats --json"]

    def test_recalled_history_line_gets_no_self_suggestion(self, monkeypatch):
        """History navigation writes recalled lines into the Input, which
        triggers the suggester. Deliberate: the recalled line itself is never
        suggested (only a longer, more recent line may ghost past it)."""
        import asyncio

        app = _make_history_app(monkeypatch, ["/stats"])
        app._history_prev()  # recalls "/stats" into the input
        recalled = app.input_value
        try:
            suggestion = asyncio.run(app._suggester.get_suggestion(recalled))
        finally:
            asyncio.set_event_loop(asyncio.new_event_loop())
        assert suggestion is None


class TestSuggesterPilot:
    def test_typing_ghosts_and_right_arrow_accepts(self, monkeypatch):
        import asyncio
        import logging

        from textual.widgets import Input

        import ui

        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        # Belt and braces: disable the auto-sync timer so the Pilot app never
        # schedules background work during the test.
        monkeypatch.setenv("TUNR_AUTO_SYNC_MINUTES", "0")
        app = PlaylistInteractiveApp(cli=PlaylistCLI(), parser=setup_parsers())

        # on_mount's configure_logging replaces the root handlers AND raises
        # the root level to INFO; restore both afterwards so later tests log
        # normally (a leaked INFO level makes caplog-based tests see records
        # they never see on a clean run).
        root_logger = logging.getLogger()
        saved_handlers = list(root_logger.handlers)
        saved_level = root_logger.level

        async def drive():
            async with app.run_test(size=(90, 30)) as pilot:
                command_input = app.query_one(Input)
                await pilot.press(*"/up")
                await pilot.pause()  # let the suggester worker deliver
                assert command_input._suggestion == "/update"
                await pilot.press("right")  # cursor at end -> accept ghost
                assert command_input.value == "/update"

        try:
            asyncio.run(drive())
        finally:
            # py3.9: restore a current event loop after asyncio.run (repo
            # pattern, see tests/test_dashboard.py TestPilotSmoke).
            asyncio.set_event_loop(asyncio.new_event_loop())
            root_logger.handlers = saved_handlers
            root_logger.setLevel(saved_level)
            # on_mount installed the app as the global ui sinks; Textual 8
            # never fires on_shutdown under run_test, so drop them here or
            # every later ui-emitting test talks to a dead app.
            ui.set_output_sink(None)
            ui.set_preview_sink(None)


# ctrl+p command palette: inventory, classification, callbacks, curation
# ============================================================================


def _run_async(coro):
    """asyncio.run + event-loop restoration (py3.9 leaves none behind)."""
    import asyncio

    try:
        return asyncio.run(coro)
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _palette_by_name(app):
    return {entry.name: entry for entry in app._palette_commands()}


class TestPaletteInventory:
    def test_inventory_covers_registry_and_meta(self, monkeypatch):
        app = _make_app(monkeypatch)
        names = {entry.name for entry in app._palette_commands()}
        registry = {name for name, _ in app._command_summaries()}
        meta = set(app._meta_command_names())
        assert names == registry | meta
        assert len(names) == len(app._palette_commands())  # no duplicates

    def test_every_entry_has_help(self, monkeypatch):
        app = _make_app(monkeypatch)
        for entry in app._palette_commands():
            assert entry.help, f"/{entry.name} has no one-liner"

    def test_hidden_commands_stay_hidden(self, monkeypatch):
        app = _make_app(monkeypatch)
        names = {entry.name for entry in app._palette_commands()}
        for hidden in ("interactive", "rotate-played", "?"):
            assert hidden not in names

    def test_key_commands_present(self, monkeypatch):
        app = _make_app(monkeypatch)
        names = {entry.name for entry in app._palette_commands()}
        assert {"update", "search", "backup", "dash", "help", "quit"} <= names


class TestPaletteClassification:
    def test_positional_commands_insert(self, monkeypatch):
        by_name = _palette_by_name(_make_app(monkeypatch))
        for name in ("update", "view", "sync", "rotate", "search", "find", "import-history"):
            assert by_name[name].needs_argument, f"/{name} should preload into the input"

    def test_optional_positional_still_inserts(self, monkeypatch):
        """Safest default: /backup's name is nargs='?' but effectively wanted."""
        by_name = _palette_by_name(_make_app(monkeypatch))
        for name in ("backup", "ingest", "restore-previous-rotation"):
            assert by_name[name].needs_argument

    def test_no_arg_registry_commands_submit(self, monkeypatch):
        by_name = _palette_by_name(_make_app(monkeypatch))
        for name in (
            "stats",
            "profile",
            "taste",
            "clean",
            "undo",
            "enrich",
            "sonic",
            "listen-sync",
            "pull",
            "list-backups",
            "auth-status",
            "auth-refresh",
        ):
            assert not by_name[name].needs_argument, f"/{name} is provably no-arg"

    def test_meta_commands_submit(self, monkeypatch):
        by_name = _palette_by_name(_make_app(monkeypatch))
        for name in ("help", "dash", "clear", "quit", "env", "debug", "expand"):
            assert not by_name[name].needs_argument


class PaletteApp(HistoryApp):
    """HistoryApp (input seams) plus a recorded _focus_input."""

    def __init__(self, cli, parser):
        super().__init__(cli=cli, parser=parser)
        self.focus_calls = 0

    def _focus_input(self) -> None:
        self.focus_calls += 1


def _make_palette_app(monkeypatch, with_spotify=True):
    if with_spotify:
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
    else:
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.delenv(key, raising=False)
    app = PaletteApp(cli=PlaylistCLI(), parser=setup_parsers())
    app._refresh_env_status()
    return app


class TestPaletteCallbacks:
    def test_submit_routes_through_command_path(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._palette_submit("stats")
        assert app.commands == ["stats"]
        assert "> /stats" in _logged_text(app)

    def test_submit_records_history(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._palette_submit("stats")
        assert app._history == ["/stats"]

    def test_submit_meta_command(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._palette_submit("quit")
        assert app._quit_called is True

    def test_submit_respects_setup_gate(self, monkeypatch):
        app = _make_app(monkeypatch, with_spotify=False)
        app._palette_submit("auth-status")  # not in the no-Spotify allowlist
        assert app.commands == []
        assert "Spotify keys missing" in _logged_text(app)

    def test_submit_allowed_without_spotify(self, monkeypatch):
        app = _make_app(monkeypatch, with_spotify=False)
        app._palette_submit("stats")
        assert app.commands == ["stats"]

    def test_submit_dismisses_armed_wizard(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.last_search_track_ids = ["artist|||song"]
        app.cli.last_search_query = "indie"
        app._prompt_search_followup()
        app._palette_submit("stats")
        assert app._pending_action is None
        assert "Search follow-up dismissed." in _logged_text(app)
        assert "stats" in app.commands

    def test_submit_respects_busy_gate(self, monkeypatch):
        class GateApp(DummyApp):
            _run_command = PlaylistInteractiveApp._run_command

        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = GateApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        app.status = "running /search"
        app.run_worker = lambda *a, **k: pytest.fail("worker must not start while busy")
        app._palette_submit("stats")
        assert "Another command is already running." in _logged_text(app)

    def test_insert_preloads_input_without_dispatch(self, monkeypatch):
        app = _make_palette_app(monkeypatch)
        app._palette_insert("update")
        assert app.input_value == "/update "
        assert app.focus_calls == 1
        assert app.commands == []
        assert app._history == []
        assert app.logged == []


class _FakeScreen:
    """Bare screen stand-in: Provider only needs .app off it."""

    def __init__(self, app):
        self.app = app


class _StackedPaletteApp(PaletteApp):
    """PaletteApp with a controllable screen stack for provider guards."""

    fake_stack = ()

    @property
    def screen_stack(self):
        return list(self.fake_stack)


def _provider_for(app, screen=None):
    from interactive_app import TunrCommandProvider

    return TunrCommandProvider(screen if screen is not None else _FakeScreen(app))


class TestPaletteProvider:
    def test_search_yields_matching_hit(self, monkeypatch):
        app = _make_palette_app(monkeypatch)
        provider = _provider_for(app)

        async def collect():
            return [hit async for hit in provider.search("update")]

        hits = _run_async(collect())
        assert any(hit.text == "/update" for hit in hits)
        # Ranked hits carry the command's one-liner as help text.
        update_hit = next(hit for hit in hits if hit.text == "/update")
        assert update_hit.help

    def test_search_no_match_yields_nothing(self, monkeypatch):
        app = _make_palette_app(monkeypatch)
        provider = _provider_for(app)

        async def collect():
            return [hit async for hit in provider.search("zzzzzqqqqq")]

        assert _run_async(collect()) == []

    def test_discover_lists_full_inventory(self, monkeypatch):
        app = _make_palette_app(monkeypatch)
        provider = _provider_for(app)

        async def collect():
            return [hit async for hit in provider.discover()]

        hits = _run_async(collect())
        assert {hit.text for hit in hits} == {f"/{entry.name}" for entry in app._palette_commands()}

    def test_hit_callback_inserts_arg_command(self, monkeypatch):
        app = _make_palette_app(monkeypatch)
        provider = _provider_for(app)

        async def collect():
            return [hit async for hit in provider.search("update")]

        hits = _run_async(collect())
        next(hit for hit in hits if hit.text == "/update").command()
        assert app.input_value == "/update "
        assert app.commands == []

    def test_hit_callback_submits_no_arg_command(self, monkeypatch):
        app = _make_palette_app(monkeypatch)
        provider = _provider_for(app)

        async def collect():
            return [hit async for hit in provider.search("stats")]

        hits = _run_async(collect())
        next(hit for hit in hits if hit.text == "/stats").command()
        assert app.commands == ["stats"]
        assert app.input_value == ""

    def test_provider_offers_nothing_over_pushed_screen(self, monkeypatch):
        """Opened over /dash the input is unreachable and commands would race
        the modal's DB reads: yield nothing (system commands still show)."""
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = _StackedPaletteApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        base, modal = _FakeScreen(app), _FakeScreen(app)
        app.fake_stack = (base, modal)
        provider = _provider_for(app, screen=modal)

        async def collect():
            searched = [hit async for hit in provider.search("update")]
            discovered = [hit async for hit in provider.discover()]
            return searched, discovered

        searched, discovered = _run_async(collect())
        assert searched == []
        assert discovered == []

    def test_provider_active_on_base_screen(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = _StackedPaletteApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        base = _FakeScreen(app)
        app.fake_stack = (base,)
        provider = _provider_for(app, screen=base)

        async def collect():
            return [hit async for hit in provider.search("update")]

        assert _run_async(collect())


class TestSystemCommandsCuration:
    def _titles(self, monkeypatch):
        from types import SimpleNamespace

        app = _make_app(monkeypatch)
        screen = SimpleNamespace(query=lambda selector: [], maximized=None, focused=None)
        return [command.title for command in app.get_system_commands(screen)]

    def test_theme_switcher_removed(self, monkeypatch):
        titles = self._titles(monkeypatch)
        assert all("theme" not in title.lower() for title in titles)

    def test_quit_keys_screenshot_kept(self, monkeypatch):
        titles = self._titles(monkeypatch)
        assert {"Quit", "Keys", "Screenshot"} <= set(titles)

    def test_provider_registered_on_app(self):
        from interactive_app import TunrCommandProvider

        assert TunrCommandProvider in PlaylistInteractiveApp.COMMANDS
        # The stock providers (system commands) are kept, not replaced.
        assert PlaylistInteractiveApp.COMMANDS > {TunrCommandProvider}


class TestSubmitTextRegression:
    """The on_input_submitted -> _submit_text refactor must not change the
    typed-input path: meta routing, gating, and history semantics are pinned
    elsewhere; these cover the seam itself."""

    def test_submit_text_strips_and_ignores_empty(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._submit_text("   ")
        assert app.commands == []
        assert app.logged == []
        assert app._history == []

    def test_on_input_submitted_delegates_to_submit_text(self, monkeypatch):
        app = _make_app(monkeypatch)
        seen = []
        app._submit_text = seen.append
        app.on_input_submitted(_SubmitEvent("/stats"))
        assert seen == ["/stats"]

    def test_pending_non_slash_bypasses_submit_text(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.last_search_track_ids = ["artist|||song"]
        app.cli.last_search_query = "indie"
        app._prompt_search_followup()
        app._submit_text = lambda raw: pytest.fail("wizard answers must not submit")
        app.on_input_submitted(_SubmitEvent("yes"))
        assert app._pending_action == "search_action"

    def test_submit_text_resets_history_navigation(self, monkeypatch):
        app = _make_history_app(monkeypatch, ["/stats"])
        app._history_prev()
        assert app._navigating is True
        app._submit_text("/env")
        assert app._navigating is False
        assert app._nav_placed_value is None
        assert app._history_prefix == ""
        assert app._history_index is None


# ---------------------------------------------------------------------------
# Pilot smoke: ctrl+p opens the palette on the real app, escape closes it
# ---------------------------------------------------------------------------


class TestPaletteSmoke:
    def test_ctrl_p_opens_and_escape_closes(self, monkeypatch):
        import logging as _logging

        from textual.command import CommandPalette

        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        monkeypatch.setenv("TUNR_AUTO_SYNC_MINUTES", "0")  # keep the run inert

        async def drive():
            app = PlaylistInteractiveApp(cli=PlaylistCLI(), parser=setup_parsers())
            async with app.run_test(size=(100, 30)) as pilot:
                assert not CommandPalette.is_open(app)
                await pilot.press("ctrl+p")
                await pilot.pause()
                assert CommandPalette.is_open(app)
                await pilot.press("escape")
                await pilot.pause()
                assert not CommandPalette.is_open(app)

        # on_mount runs configure_logging (replaces root handlers, sets the
        # root level to INFO) and points the global ui sinks at this app;
        # undo all of it afterwards so later tests log and emit normally.
        import ui as _ui

        root = _logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        try:
            _run_async(drive())
        finally:
            root.handlers = saved_handlers
            root.setLevel(saved_level)
            _ui.set_output_sink(None)
            _ui.set_preview_sink(None)


# ============================================================================
# Esc-to-cancel: the run-generation guard, the cancel action, and gates
# ============================================================================


class _FakeWorker:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class TestEscCancelUnit:
    def _app(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = DummyApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        return app

    def test_escape_when_idle_is_noop(self, monkeypatch):
        app = self._app(monkeypatch)
        gen = app._run_generation
        app.action_cancel_command()
        assert app.status == "idle"
        assert app._run_generation == gen  # no spurious invalidation
        assert app.logged == []

    def test_cancel_flags_worker_flips_status_and_logs_honest_line(self, monkeypatch):
        import time as _time

        app = self._app(monkeypatch)
        app.status = "running /search"
        app._run_started = _time.monotonic()
        app._stage = "extract 1/10"
        worker = _FakeWorker()
        app._active_worker = worker
        gen = app._run_generation

        app.action_cancel_command()

        assert worker.cancelled is True
        assert app.status == "cancelled"
        assert app._active_worker is None
        assert app._run_generation == gen + 1
        assert app._run_started is None
        assert app._stage == ""
        assert app._last_run_note == "last: /search cancelled"
        text = _logged_text(app)
        assert "Cancelled /search" in text
        assert "background" in text  # honest thread-worker caveat

    def test_gate_blocks_while_cancelled_thread_unwinds_then_queues(self, monkeypatch):
        class GateApp(DummyApp):
            _run_command = PlaylistInteractiveApp._run_command

        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = GateApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        app.run_worker = lambda fn, thread=True: _FakeWorker()

        # Esc-cancelled run whose thread is still unwinding: new work must
        # NOT start — it would overlap the stale thread on the shared
        # serialized-use sqlite connection (and possibly the same playlist).
        # It is HELD in the single-slot queue instead of refused.
        app.status = "cancelled"
        app._inflight_workers = 1
        app._run_generation = 3  # the cancelled run held a stale generation
        app._run_command("stats", object())
        assert app.status == "cancelled"  # gate stays closed — nothing started
        assert app._queued_command is not None
        assert "Queued /stats" in _logged_text(app)

        # The stale thread unwinds: true idle is restored and the queued
        # command starts THROUGH the same gate (dequeue-at-true-idle).
        app._worker_thread_exited(2)
        assert app.status == "running /stats"
        assert app._queued_command is None
        assert "Starting queued /stats" in _logged_text(app)

        # …running: the next submission takes the (now empty) queue slot,
        # and a second one is refused while the slot is full.
        app._run_command("stats", object())
        assert app._queued_command is not None
        app._run_command("stats", object())
        assert "/stats is queued" in _logged_text(app)

    def test_is_idle_requires_zero_inflight_threads(self, monkeypatch):
        app = self._app(monkeypatch)
        assert app._is_idle()
        app._inflight_workers = 1
        assert not app._is_idle()  # "idle" status alone is not enough
        app.status = "cancelled"
        assert not app._is_idle()
        app._inflight_workers = 0
        assert app._is_idle()  # rest status + fully unwound

    def test_refuse_messages_distinguish_cancelled_from_finishing(self, monkeypatch):
        app = self._app(monkeypatch)
        app._inflight_workers = 1
        app.status = "cancelled"
        assert app._refuse_if_busy() is True
        assert "Waiting for the cancelled command" in _logged_text(app)
        app.logged.clear()
        # _post_command already restored "idle" from the worker thread, but
        # the thread's exit notification hasn't landed yet.
        app.status = "idle"
        assert app._refuse_if_busy() is True
        assert "still finishing" in _logged_text(app)
        app.logged.clear()
        app.status = "running /stats"
        assert app._refuse_if_busy() is True
        assert "Another command is already running" in _logged_text(app)
        app.logged.clear()
        app._inflight_workers = 0
        app.status = "idle"
        assert app._refuse_if_busy() is False
        assert app.logged == []

    def test_run_if_current_drops_stale_runs_current(self, monkeypatch):
        app = self._app(monkeypatch)
        app._run_generation = 5
        calls = []
        app._run_if_current(4, calls.append, "stale")
        assert calls == []
        app._run_if_current(5, calls.append, "current")
        assert calls == ["current"]

    def test_dispatch_ui_guards_thread_bound_generation(self, monkeypatch):
        app = self._app(monkeypatch)
        calls = []
        app._worker_gen.gen = 1  # simulate a worker-bound thread
        app._run_generation = 2  # …whose run has been cancelled
        app._dispatch_ui(calls.append, "stale")
        assert calls == []
        app._run_generation = 1  # generation current again
        app._dispatch_ui(calls.append, "current")
        assert calls == ["current"]

    def test_dispatch_ui_unbound_thread_never_guarded(self, monkeypatch):
        app = self._app(monkeypatch)
        app._run_generation = 99  # no thread-local gen bound -> no guard
        calls = []
        app._dispatch_ui(calls.append, "auto-sync line")
        assert calls == ["auto-sync line"]

    def test_worker_thread_exited_restores_idle_at_zero_inflight(self, monkeypatch):
        app = self._app(monkeypatch)
        app.status = "cancelled"
        app._inflight_workers = 2
        app._run_generation = 7
        app._worker_thread_exited(3)  # stale thread unwinds first
        assert app.status == "cancelled"  # one thread still in flight
        app._worker_thread_exited(3)
        assert app.status == "idle"
        assert app._inflight_workers == 0

    def test_worker_thread_exited_clears_active_worker_only_for_current_gen(self, monkeypatch):
        app = self._app(monkeypatch)
        current = _FakeWorker()
        app._active_worker = current
        app._run_generation = 7
        app._inflight_workers = 2
        app._worker_thread_exited(3)  # stale exit must not touch the new handle
        assert app._active_worker is current
        app._worker_thread_exited(7)
        assert app._active_worker is None

    def test_worker_thread_exited_never_flips_a_running_status(self, monkeypatch):
        app = self._app(monkeypatch)
        app.status = "running /stats"
        app._inflight_workers = 1
        app._worker_thread_exited(0)
        assert app.status == "running /stats"

    def test_run_user_work_binds_generation_and_notifies_exit(self, monkeypatch):
        app = self._app(monkeypatch)
        seen = []
        app._run_generation = 4
        app._inflight_workers = 1  # as _start_user_worker would have set
        app._run_user_work(4, lambda: seen.append(getattr(app._worker_gen, "gen", None)))
        assert seen == [4]  # generation visible to the work
        assert getattr(app._worker_gen, "gen", None) is None  # cleared (pooled threads)
        assert app._inflight_workers == 0  # exit notification delivered

    def test_cancelled_status_does_not_animate_spinner(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = SpinnerRecordingApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        app.status = "running /stats"
        app.spinner_calls.clear()
        app.status = "cancelled"
        assert app.spinner_calls == ["stop"]


class TestEscCancelStaleWorkerOutput:
    """A cancelled run's worker thread must not write late output anywhere."""

    def _worker_app(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = WorkerApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        app.cli.last_search_results = None
        return app

    def _make_stale(self, app):
        """Bind this (test) thread to a generation that is no longer current."""
        app._worker_gen.gen = 1
        app._run_generation = 2

    def test_stale_execute_command_emits_nothing(self, monkeypatch):
        import time as _time

        import interactive_app as ia

        app = self._worker_app(monkeypatch)
        monkeypatch.setattr(ia, "dispatch_command", lambda cli, cmd, args: 0)
        self._make_stale(app)
        app.status = "cancelled"
        app._run_started = _time.monotonic() - 20
        app._execute_command("stats", object())
        assert app.status == "cancelled"  # _post_command suppressed
        assert "finished in" not in _logged_text(app)
        assert app.notifications == []  # slow-command toast suppressed

    def test_stale_failure_lines_and_toast_suppressed(self, monkeypatch):
        import interactive_app as ia

        app = self._worker_app(monkeypatch)
        monkeypatch.setattr(ia, "dispatch_command", lambda cli, cmd, args: 1)
        self._make_stale(app)
        app.status = "cancelled"
        app._execute_command("stats", object())
        assert "exited with errors" not in _logged_text(app)
        assert app.notifications == []
        assert app.status == "cancelled"

    def test_stale_error_records_dropped_and_not_counted(self, monkeypatch):
        import logging as _logging
        import threading as _threading

        from interactive_app import UILogHandler

        app = self._worker_app(monkeypatch)
        app._app_thread_id = _threading.get_ident()
        self._make_stale(app)
        handler = UILogHandler(app)
        record = _logging.LogRecord("t", _logging.ERROR, __file__, 1, "late boom", None, None)
        handler.emit(record)
        assert app._command_error_count == 0  # new command's window stays clean
        assert "late boom" not in _logged_text(app)  # scrollback line dropped

    def test_current_error_records_still_counted(self, monkeypatch):
        import logging as _logging
        import threading as _threading

        from interactive_app import UILogHandler

        app = self._worker_app(monkeypatch)
        app._app_thread_id = _threading.get_ident()
        app._worker_gen.gen = app._run_generation  # bound and current
        handler = UILogHandler(app)
        record = _logging.LogRecord("t", _logging.ERROR, __file__, 1, "boom", None, None)
        handler.emit(record)
        assert app._command_error_count == 1
        assert "boom" in _logged_text(app)


# ---------------------------------------------------------------------------
# Pilot: Esc cancels the live worker; pushed-screen Esc keeps priority; the
# stale worker's late output never reaches the scrollback. The blocked
# handler is gated on threading.Events so the test controls all timing.
# ---------------------------------------------------------------------------


class TestEscCancelPilot:
    def test_escape_cancels_running_command_end_to_end(self, monkeypatch):
        import logging as _logging
        import threading as _threading

        from textual.command import CommandPalette
        from textual.widgets import RichLog

        import interactive_app as ia
        import ui as _ui

        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        monkeypatch.setenv("TUNR_AUTO_SYNC_MINUTES", "0")  # keep the run inert

        gate = _threading.Event()  # blocks the first /stats until the test says go
        entered = _threading.Event()  # first /stats reached the handler
        finished = _threading.Event()  # first /stats fully unwound
        calls = []

        def _dispatch(cli, command, args):
            calls.append(command)
            if len(calls) == 1:
                entered.set()
                gate.wait(timeout=10)
                # Late output from the cancelled run: must be swallowed.
                _ui.info("stale output after cancel")
                finished.set()
            return 0

        monkeypatch.setattr(ia, "dispatch_command", _dispatch)

        def _scrollback(app) -> str:
            log = app.query_one(RichLog)
            return "\n".join(strip.text for strip in log.lines)

        async def drive():
            app = PlaylistInteractiveApp(cli=PlaylistCLI(), parser=setup_parsers())
            async with app.run_test(size=(100, 30)) as pilot:
                app._submit_text("/stats")
                for _ in range(500):  # bounded wait on a test-owned Event
                    if entered.is_set():
                        break
                    await pilot.pause(0.01)
                assert entered.is_set()
                assert app.status == "running /stats"

                # Pushed screens keep Esc priority: the palette's own Escape
                # closes it and must NOT cancel the running command.
                await pilot.press("ctrl+p")
                await pilot.pause()
                assert CommandPalette.is_open(app)
                await pilot.press("escape")
                await pilot.pause()
                assert not CommandPalette.is_open(app)
                assert app.status == "running /stats"

                # Esc on the main screen cancels the worker.
                worker = app._active_worker
                assert worker is not None
                await pilot.press("escape")
                assert app.status == "cancelled"
                assert worker.is_cancelled
                assert app._active_worker is None
                assert "Cancelled /stats" in _scrollback(app)

                # The gate stays CLOSED while the cancelled thread unwinds:
                # a second /stats must not START a worker that would share
                # the serialized-use sqlite connection with the stale thread.
                # It is held in the single-slot queue instead.
                app._submit_text("/stats")
                await pilot.pause()
                assert calls == ["stats"]  # nothing new dispatched
                assert app.status == "cancelled"
                assert app._queued_command is not None
                assert "Queued /stats" in _scrollback(app)

                # Release the cancelled worker: its late output and completion
                # must be suppressed, and once true idle is restored the
                # queued /stats starts on its own — never a moment earlier.
                gate.set()
                for _ in range(500):
                    if finished.is_set():
                        break
                    await pilot.pause(0.01)
                assert finished.is_set()
                for _ in range(500):  # drain: stale exit -> true idle -> dequeue
                    if len(calls) == 2 and app.status == "idle" and app._inflight_workers == 0:
                        break
                    await pilot.pause(0.01)
                text = _scrollback(app)
                assert "stale output after cancel" not in text
                assert calls == ["stats", "stats"]  # the queued run, nothing else
                assert app._queued_command is None
                assert "Starting queued /stats" in text
                assert app.status == "idle"
                assert app._inflight_workers == 0
                # Exactly one completion line: the cancelled run stays silent,
                # the dequeued run reports normally.
                assert text.count("finished in") == 1

        root = _logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        try:
            _run_async(drive())
        finally:
            root.handlers = saved_handlers
            root.setLevel(saved_level)
            _ui.set_output_sink(None)
            _ui.set_preview_sink(None)
            _ui.set_status_sink(None)


# ============================================================================
# Destructive-command confirm modal (TUI dispatch path only)
# ============================================================================


class _ConfirmGateApp(DummyApp):
    """DummyApp with the REAL _run_command so the modal gate is under test;
    push_screen and _start_command are captured instead of executed."""

    _run_command = PlaylistInteractiveApp._run_command

    def __init__(self, cli, parser):
        super().__init__(cli=cli, parser=parser)
        self.pushed = []
        self.started = []

    def push_screen(self, screen, callback=None):
        self.pushed.append((screen, callback))

    def _start_command(self, command, args):
        self.started.append((command, args))

    def _focus_input(self):
        pass


class TestDestructiveQuestion:
    def _app(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = DummyApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        return app

    def test_destructive_commands_get_questions(self, monkeypatch):
        from types import SimpleNamespace

        question = self._app(monkeypatch)._destructive_question
        assert "backup 'b1'" in question("restore", SimpleNamespace(backup_name="b1"))
        assert "token" in question("auth-reset", SimpleNamespace(yes=False))
        assert "rewrites" in question("update", SimpleNamespace(playlist="Chill", dry_run=False))
        assert "Rotate" in question("rotate", SimpleNamespace(playlist="Chill"))
        assert "Rotate" in question("rotate-played", SimpleNamespace(playlist="Chill"))
        assert "mirrors the db" in question("sync", SimpleNamespace(playlist="Chill"))
        assert "Permanently" in question("clean", SimpleNamespace(dry_run=False))

    def test_dry_runs_are_not_gated(self, monkeypatch):
        from types import SimpleNamespace

        question = self._app(monkeypatch)._destructive_question
        assert question("update", SimpleNamespace(playlist="Chill", dry_run=True)) is None
        assert question("rotate", SimpleNamespace(playlist="Chill", dry_run=True)) is None
        assert question("clean", SimpleNamespace(dry_run=True)) is None

    def test_non_destructive_and_recovery_commands_are_not_gated(self, monkeypatch):
        from types import SimpleNamespace

        question = self._app(monkeypatch)._destructive_question
        args = SimpleNamespace(playlist="Chill", backup_name="b1", top=5, offset=-1)
        for command in (
            "stats",
            "view",
            "plan",
            "diff",
            "backup",
            "search",
            "find",
            "auth-status",
            # Recovery commands stay friction-free on purpose:
            "undo",
            "restore-previous-rotation",
        ):
            assert question(command, args) is None, command


class TestConfirmGateUnit:
    def _app(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = _ConfirmGateApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        return app

    def test_non_destructive_starts_without_modal(self, monkeypatch):
        app = self._app(monkeypatch)
        args = object()
        app._run_command("stats", args)
        assert app.pushed == []
        assert app.started == [("stats", args)]

    def test_destructive_waits_for_confirmation(self, monkeypatch):
        from types import SimpleNamespace

        from interactive_app import ConfirmScreen

        app = self._app(monkeypatch)
        args = SimpleNamespace(playlist="Chill")
        app._run_command("rotate", args)
        assert app.started == []  # nothing runs until the modal answers
        assert len(app.pushed) == 1
        screen, callback = app.pushed[0]
        assert isinstance(screen, ConfirmScreen)
        callback(True)
        assert app.started == [("rotate", args)]

    def test_cancel_leaves_command_unrun_and_logs(self, monkeypatch):
        from types import SimpleNamespace

        app = self._app(monkeypatch)
        app._run_command("restore", SimpleNamespace(backup_name="b1"))
        _screen, callback = app.pushed[0]
        callback(False)
        assert app.started == []
        assert "/restore cancelled — nothing changed." in _logged_text(app)

    def test_confirmed_auth_reset_forces_yes(self, monkeypatch):
        from types import SimpleNamespace

        app = self._app(monkeypatch)
        args = SimpleNamespace(yes=False)
        app._run_command("auth-reset", args)
        _screen, callback = app.pushed[0]
        callback(True)
        assert args.yes is True  # the modal IS the TUI confirmation
        assert app.started == [("auth-reset", args)]

    def test_busy_gate_precedes_modal(self, monkeypatch):
        from types import SimpleNamespace

        app = self._app(monkeypatch)
        app.status = "running /stats"
        app._run_command("rotate", SimpleNamespace(playlist="Chill"))
        assert app.pushed == []
        assert app.started == []
        assert "Another command is already running" in _logged_text(app)


# ---------------------------------------------------------------------------
# Pilot: real modal end-to-end — confirm runs, cancel doesn't, and Esc in the
# modal closes only the modal (never the app-level Esc-cancel binding).
# ---------------------------------------------------------------------------


class TestConfirmModalPilot:
    def test_confirm_cancel_and_esc_paths(self, monkeypatch):
        import logging as _logging

        from textual.widgets import RichLog

        import interactive_app as ia
        import ui as _ui
        from interactive_app import ConfirmScreen

        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        monkeypatch.setenv("TUNR_AUTO_SYNC_MINUTES", "0")  # keep the run inert

        calls = []

        def _dispatch(cli, command, args):
            calls.append(command)
            return 0

        monkeypatch.setattr(ia, "dispatch_command", _dispatch)

        def _scrollback(app) -> str:
            log = app.query_one(RichLog)
            return "\n".join(strip.text for strip in log.lines)

        async def drive():
            app = PlaylistInteractiveApp(cli=PlaylistCLI(), parser=setup_parsers())
            async with app.run_test(size=(100, 30)) as pilot:
                # Spy on the app-level Esc cancel action: the modal's own
                # Escape binding must win, so this must never fire.
                cancel_calls = []
                orig_cancel = app.action_cancel_command

                def _spy_cancel():
                    cancel_calls.append(1)
                    orig_cancel()

                app.action_cancel_command = _spy_cancel

                # 1) Esc inside the modal closes ONLY the modal.
                app._submit_text("/rotate Chill")
                await pilot.pause()
                assert isinstance(app.screen, ConfirmScreen)
                assert calls == []  # nothing dispatched while the modal is up
                await pilot.press("escape")
                await pilot.pause()
                assert not isinstance(app.screen, ConfirmScreen)
                assert calls == []
                assert cancel_calls == []  # app-level Esc binding never fired
                assert app.status == "idle"
                assert "/rotate cancelled — nothing changed." in _scrollback(app)

                # 2) y confirms and the command actually runs.
                app._submit_text("/rotate Chill")
                await pilot.pause()
                assert isinstance(app.screen, ConfirmScreen)
                await pilot.press("y")
                for _ in range(500):
                    if calls == ["rotate"] and app.status == "idle" and app._inflight_workers == 0:
                        break
                    await pilot.pause(0.01)
                assert calls == ["rotate"]
                assert app.status == "idle"
                assert "/rotate finished in" in _scrollback(app)

                # 3) Enter must NOT confirm: "no" is focused on mount and
                # the screen binds no Enter, so the queued second Enter of an
                # accidental double-tap presses the SAFE button and cancels.
                app._submit_text("/restore b1")
                await pilot.pause()
                assert isinstance(app.screen, ConfirmScreen)
                await pilot.press("enter")  # the double-tap's second Enter
                await pilot.pause()
                assert not isinstance(app.screen, ConfirmScreen)
                assert calls == ["rotate"]  # restore never dispatched
                assert "/restore cancelled — nothing changed." in _scrollback(app)

                # 3b) The deliberate keyboard route to yes still works: tab
                # moves focus to the yes button, enter presses it.
                app._submit_text("/restore b1")
                await pilot.pause()
                assert isinstance(app.screen, ConfirmScreen)
                await pilot.press("tab")
                await pilot.press("enter")
                for _ in range(500):
                    if (
                        calls == ["rotate", "restore"]
                        and app.status == "idle"
                        and app._inflight_workers == 0
                    ):
                        break
                    await pilot.pause(0.01)
                assert calls == ["rotate", "restore"]

                # 4) n cancels: no further dispatch.
                app._submit_text("/rotate Chill")
                await pilot.pause()
                assert isinstance(app.screen, ConfirmScreen)
                await pilot.press("n")
                await pilot.pause()
                assert not isinstance(app.screen, ConfirmScreen)
                assert calls == ["rotate", "restore"]
                assert app.status == "idle"

        # on_mount replaces root logging handlers and points the ui sinks at
        # this app; restore both afterwards (same pattern as the Esc pilot).
        root = _logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        try:
            _run_async(drive())
        finally:
            root.handlers = saved_handlers
            root.setLevel(saved_level)
            _ui.set_output_sink(None)
            _ui.set_preview_sink(None)
            _ui.set_status_sink(None)


# ============================================================================
# Single-slot command queue: enqueue, refusal, dequeue-at-true-idle, Esc
# ============================================================================


class _QueueApp(DummyApp):
    """DummyApp with the REAL queue machinery under test.

    `_run_command` is the real gate; workers are faked and `_start_command` /
    `push_screen` are captured, so tests can observe exactly what would start
    (and which modal would be shown) without Textual running.
    """

    _run_command = PlaylistInteractiveApp._run_command

    def __init__(self, cli, parser):
        super().__init__(cli=cli, parser=parser)
        self.pushed = []
        self.started = []

    def run_worker(self, fn, thread=True):
        return _FakeWorker()

    def push_screen(self, screen, callback=None):
        self.pushed.append((screen, callback))

    def _start_command(self, command, args):
        self.started.append((command, args))

    def _focus_input(self):
        pass


class TestCommandQueueUnit:
    def _app(self, monkeypatch):
        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        app = _QueueApp(cli=PlaylistCLI(), parser=setup_parsers())
        app._refresh_env_status()
        return app

    def _busy(self, app):
        """State exactly as _start_user_worker leaves it mid-run."""
        app.status = "running /search"
        app._inflight_workers = 1
        app._active_worker = _FakeWorker()

    def test_submission_while_running_enqueues(self, monkeypatch):
        app = self._app(monkeypatch)
        self._busy(app)
        args = object()
        app._run_command("stats", args)
        assert app._queued_command == ("stats", args)
        assert app.started == []  # queued, not started
        assert "Queued /stats" in _logged_text(app)

    def test_second_submission_while_queued_is_refused(self, monkeypatch):
        app = self._app(monkeypatch)
        self._busy(app)
        app._run_command("stats", object())
        app.logged.clear()
        app._run_command("profile", object())
        assert app._queued_command[0] == "stats"  # slot unchanged
        assert app.started == []
        text = _logged_text(app)
        assert "Another command is already running" in text  # existing honest style
        assert "/stats is queued" in text

    def test_queued_command_starts_only_at_true_idle(self, monkeypatch):
        app = self._app(monkeypatch)
        self._busy(app)
        args = object()
        app._run_command("stats", args)
        # Worker-side completion restores the status first (_post_command)…
        app._post_command("search")
        assert app.status == "idle"
        assert app.started == []  # …but the thread has not exited: still held
        # …and only the exit notification (TRUE idle) dequeues.
        app._worker_thread_exited(app._run_generation)
        assert app.started == [("stats", args)]
        assert app._queued_command is None
        assert "Starting queued /stats" in _logged_text(app)

    def test_nothing_dequeues_while_cancelled_worker_unwinds(self, monkeypatch):
        app = self._app(monkeypatch)
        app.status = "cancelled"
        app._inflight_workers = 2  # two stale threads still unwinding
        app._run_command("stats", object())
        assert app._queued_command is not None
        app._worker_thread_exited(0)
        assert app.status == "cancelled"  # one thread left: not true idle
        assert app.started == []
        assert app._queued_command is not None
        app._worker_thread_exited(0)  # last thread unwinds -> true idle
        assert app._queued_command is None
        assert app.started and app.started[0][0] == "stats"

    def test_escape_cancels_running_and_clears_queue_saying_both(self, monkeypatch):
        app = self._app(monkeypatch)
        self._busy(app)
        worker = app._active_worker
        app._run_command("stats", object())
        app.action_cancel_command()
        assert worker.cancelled is True
        assert app.status == "cancelled"
        assert app._queued_command is None
        text = _logged_text(app)
        assert "Cancelled /search" in text  # says the run was cancelled…
        assert "Dropped queued /stats as well." in text  # …AND the queue cleared

    def test_escape_with_only_queued_command_drops_it(self, monkeypatch):
        # Queued behind auto-sync: no user worker to cancel, but Esc still
        # clears the slot instead of silently ignoring the keypress.
        app = self._app(monkeypatch)
        app.status = "auto-sync"
        app._run_command("stats", object())
        assert app._queued_command is not None
        app.action_cancel_command()
        assert app._queued_command is None
        assert "Dropped queued /stats." in _logged_text(app)
        assert app.status == "auto-sync"  # background work untouched

    def test_escape_idle_empty_queue_still_noop(self, monkeypatch):
        app = self._app(monkeypatch)
        app.action_cancel_command()
        assert app.logged == []

    def test_queued_destructive_confirms_at_dequeue_not_enqueue(self, monkeypatch):
        from types import SimpleNamespace

        from interactive_app import ConfirmScreen

        app = self._app(monkeypatch)
        self._busy(app)
        args = SimpleNamespace(playlist="Chill")
        app._run_command("rotate", args)
        assert app.pushed == []  # NO modal at enqueue time
        assert app._queued_command == ("rotate", args)
        # Cancel-unwind path to true idle: modal must appear only now.
        app.status = "cancelled"
        app._active_worker = None
        app._worker_thread_exited(0)
        assert app.started == []  # nothing runs before the answer
        assert len(app.pushed) == 1
        screen, callback = app.pushed[0]
        assert isinstance(screen, ConfirmScreen)
        callback(True)
        assert app.started == [("rotate", args)]

    def test_auto_sync_finish_dequeues(self, monkeypatch):
        app = self._app(monkeypatch)
        app.status = "auto-sync"
        args = object()
        app._run_command("stats", args)
        assert app._queued_command == ("stats", args)
        app._finish_auto_sync()
        assert app.status == "idle"
        assert app.started == [("stats", args)]
        assert app._queued_command is None

    def test_auto_sync_stands_down_while_a_command_is_queued(self, monkeypatch):
        app = self._app(monkeypatch)
        app._queued_command = ("stats", object())
        launched = []
        app.run_worker = lambda *a, **k: launched.append(1)
        app._maybe_auto_sync()
        assert launched == []
        assert app.status == "idle"


# ---------------------------------------------------------------------------
# Pilot: queue lifecycle end-to-end — visible status, refusal, auto-start at
# true idle, ConfirmScreen at dequeue, Esc clearing both. Timing is owned by
# the test via threading.Events (same pattern as the Esc pilot).
# ---------------------------------------------------------------------------


class TestCommandQueuePilot:
    def test_queue_lifecycle_end_to_end(self, monkeypatch):
        import logging as _logging
        import threading as _threading
        from io import StringIO

        from rich.console import Console
        from textual.widgets import RichLog

        import interactive_app as ia
        import ui as _ui
        from interactive_app import ConfirmScreen

        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        monkeypatch.setenv("TUNR_AUTO_SYNC_MINUTES", "0")  # keep the run inert

        holders = {}
        calls = []

        def _arm():
            holders["gate"] = _threading.Event()
            holders["entered"] = _threading.Event()

        def _dispatch(cli, command, args):
            calls.append(command)
            if command == "stats":
                holders["entered"].set()
                holders["gate"].wait(timeout=10)
            return 0

        monkeypatch.setattr(ia, "dispatch_command", _dispatch)

        def _scrollback(app) -> str:
            log = app.query_one(RichLog)
            return "\n".join(strip.text for strip in log.lines)

        def _bar_text(app) -> str:
            buf = StringIO()
            Console(file=buf, width=100).print(app._render_top_bar())
            return buf.getvalue()

        async def drive():
            app = PlaylistInteractiveApp(cli=PlaylistCLI(), parser=setup_parsers())
            async with app.run_test(size=(100, 30)) as pilot:

                async def _wait(predicate):
                    for _ in range(500):
                        if predicate():
                            return
                        await pilot.pause(0.01)
                    raise AssertionError("condition never became true")

                # Round 1 — enqueue -> visible status -> refusal -> runs at idle.
                _arm()
                app._submit_text("/stats")
                await _wait(holders["entered"].is_set)
                assert app.status == "running /stats"

                app._submit_text("/profile")
                await pilot.pause()
                assert app._queued_command is not None
                assert app._queued_command[0] == "profile"
                assert "Queued /profile" in _scrollback(app)
                assert "queued: /profile" in _bar_text(app)  # status shows it

                app._submit_text("/taste")  # second submission: refused
                await pilot.pause()
                assert app._queued_command[0] == "profile"
                assert "/profile is queued" in _scrollback(app)
                assert "taste" not in calls

                holders["gate"].set()
                await _wait(
                    lambda: (
                        calls == ["stats", "profile"]
                        and app.status == "idle"
                        and app._inflight_workers == 0
                    )
                )
                assert "Starting queued /profile" in _scrollback(app)
                assert app._queued_command is None

                # Round 2 — queued destructive command confirms at DEQUEUE.
                _arm()
                app._submit_text("/stats")
                await _wait(holders["entered"].is_set)
                app._submit_text("/rotate Chill")
                await pilot.pause()
                assert app._queued_command[0] == "rotate"
                assert not isinstance(app.screen, ConfirmScreen)  # not at enqueue
                holders["gate"].set()
                await _wait(lambda: isinstance(app.screen, ConfirmScreen))
                assert calls.count("rotate") == 0  # modal up, nothing dispatched
                await pilot.press("y")
                await _wait(
                    lambda: (
                        calls.count("rotate") == 1
                        and app.status == "idle"
                        and app._inflight_workers == 0
                    )
                )

                # Round 3 — Esc cancels the running command AND clears the queue.
                _arm()
                app._submit_text("/stats")
                await _wait(holders["entered"].is_set)
                app._submit_text("/profile")
                await pilot.pause()
                assert app._queued_command is not None
                await pilot.press("escape")
                assert app.status == "cancelled"
                assert app._queued_command is None
                scrollback = _scrollback(app)
                assert "Cancelled /stats" in scrollback  # says the cancel…
                assert "Dropped queued /profile as well." in scrollback  # …and the drop
                holders["gate"].set()
                await _wait(lambda: app.status == "idle" and app._inflight_workers == 0)
                assert calls.count("profile") == 1  # round 1 only — never revived

        root = _logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        try:
            _run_async(drive())
        finally:
            root.handlers = saved_handlers
            root.setLevel(saved_level)
            _ui.set_output_sink(None)
            _ui.set_preview_sink(None)
            _ui.set_status_sink(None)


# ============================================================================
# /search & /find advisory provider pre-flight (warn, never block)
# ============================================================================


class TestSearchProviderPreflight:
    def test_search_warns_but_dispatches_without_providers(self, monkeypatch):
        from rich.panel import Panel

        monkeypatch.setattr("interactive_app.detect_search_commands", lambda: {})
        app = _make_app(monkeypatch)
        app._handle_command("/search chill jazz")
        # Advisory only: the pipeline can serve a previously-cached query with
        # zero providers (its cache check precedes run_providers), and a fresh
        # run fails fast in-pipeline with ProviderConfigError — so the
        # submission must still dispatch, keeping TUI outcomes identical to
        # the headless path.
        assert "search" in app.commands
        panels = [p for p in app.logged if isinstance(p, Panel)]
        assert panels, "expected an advisory panel"
        panel = panels[-1]
        assert panel.title == "No search provider detected"
        assert panel.border_style == "yellow"  # warning chrome, not error red
        text = _logged_text(app)
        assert "cached" in text  # explains why it still dispatched
        assert "ANTHROPIC_API_KEY" in text
        assert "OPENAI_API_KEY" in text
        assert "/env" in text  # actionable: where to verify

    def test_find_warns_but_dispatches_without_providers(self, monkeypatch):
        monkeypatch.setattr("interactive_app.detect_search_commands", lambda: {})
        app = _make_app(monkeypatch)
        app._handle_command("/find upbeat indie")
        assert "find" in app.commands
        assert "No search provider detected" in _logged_text(app)

    def test_search_dispatches_clean_with_providers(self, monkeypatch):
        monkeypatch.setattr("interactive_app.detect_search_commands", lambda: {"codex": "cmd"})
        app = _make_app(monkeypatch)
        app._handle_command("/search chill jazz")
        assert "search" in app.commands
        assert "No search provider detected" not in _logged_text(app)

    def test_find_dispatches_clean_with_providers(self, monkeypatch):
        monkeypatch.setattr("interactive_app.detect_search_commands", lambda: {"claude": "cmd"})
        app = _make_app(monkeypatch)
        app._handle_command("/find upbeat indie")
        assert "find" in app.commands

    def test_non_search_commands_never_warned(self, monkeypatch):
        monkeypatch.setattr("interactive_app.detect_search_commands", lambda: {})
        app = _make_app(monkeypatch)
        app._handle_command("/stats")
        assert "stats" in app.commands
        assert "No search provider detected" not in _logged_text(app)


# ============================================================================
# Error chrome: every inline red panel goes through ui.error_panel
# ============================================================================


class TestErrorChromeUnified:
    def test_error_panel_factory_defines_the_chrome(self):
        from rich.panel import Panel
        from rich.text import Text as RichText

        from ui import error_panel

        panel = error_panel("boom")
        assert isinstance(panel, Panel)
        assert panel.title == "Error"
        assert panel.border_style == "red"
        assert isinstance(panel.renderable, RichText)
        assert panel.renderable.plain == "boom"
        assert panel.renderable.style == "red"
        assert error_panel("x", title="Custom").title == "Custom"

    def test_ui_error_routes_factory_chrome_to_sink(self):
        import ui as _ui

        captured = []
        _ui.set_output_sink(captured.append)
        try:
            _ui.error("bad thing")
        finally:
            _ui.set_output_sink(None)
        assert len(captured) == 1
        panel = captured[0]
        assert panel.title == "Error"
        assert panel.border_style == "red"
        assert panel.renderable.plain == "bad thing"

    def test_shlex_and_unknown_command_sites_share_chrome(self, monkeypatch):
        from rich.panel import Panel

        app = _make_app(monkeypatch)
        app._handle_command("/update 'oops")  # shlex error site
        shlex_panel = app.logged[-1]
        app._handle_command("/definitely-not-a-command x")  # parse error site
        parse_panel = app.logged[-1]
        for panel in (shlex_panel, parse_panel):
            assert isinstance(panel, Panel)
            assert panel.title == "Error"
            assert panel.border_style == "red"
        assert "Invalid command syntax" in _logged_text(app)

    def test_setup_required_site_keeps_its_title_and_info(self, monkeypatch):
        from rich.panel import Panel

        app = _make_app(monkeypatch, with_spotify=False)
        app._handle_command('/view "Test"')
        panel = app.logged[-1]
        assert isinstance(panel, Panel)
        assert panel.title == "Setup Required"
        assert panel.border_style == "red"
        text = _logged_text(app)
        assert "Spotify keys missing" in text  # every original detail kept
        assert "/setup" in text
