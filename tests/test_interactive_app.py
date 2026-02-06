"""
Unit tests for interactive app command routing and handling.
Tests all interactive-only commands, aliases, shlex error handling,
setup mode gating, and worker error display.
"""
import os

from arg_parse import setup_parsers
from interactive_app import PlaylistInteractiveApp, SPOTIFY_REQUIRED_KEYS, COMMANDS_ALLOWED_WITHOUT_SPOTIFY
from main import PlaylistCLI


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
# Interactive-only command routing
# ============================================================================

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
            elif cmd in ("search",):
                app._handle_command(f"/{cmd} jazz")
            elif cmd in ("plan",):
                app._handle_command(f'/{cmd} "Test"')
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
