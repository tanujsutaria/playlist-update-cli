"""Tests for the TUI's background listen-sync (quiet, cursor-based).

The auto-sync must take the SAME `status` gate `_run_command` checks (single
shared sqlite connection), run sync_listen_history(quiet=True) on a worker,
log failures exactly once, and always release the gate. Scheduling honours
TUNR_AUTO_SYNC_MINUTES (0 disables). All offline; timers/workers are stubbed.
"""

from __future__ import annotations

import logging
import sqlite3
from unittest.mock import MagicMock

import pytest

import interactive_app
from arg_parse import setup_parsers
from interactive_app import SPOTIFY_REQUIRED_KEYS, PlaylistInteractiveApp
from main import PlaylistCLI
from spotify_manager import SPOTIFY_SCOPES
from storage.migrations import ensure_schema
from storage.repos import Repositories


class _ScopeError(Exception):
    """Mimics spotipy's insufficient-scope SpotifyException (403 + 'scope')."""

    def __init__(self) -> None:
        super().__init__("http status: 403, code:-1 - Insufficient client scope")
        self.http_status = 403


@pytest.fixture(autouse=True)
def _isolated_history(monkeypatch, tmp_path):
    monkeypatch.setenv("TUNR_HISTORY_PATH", str(tmp_path / "tunr_history"))


class AutoSyncApp(PlaylistInteractiveApp):
    """Headless harness: synchronous workers, recorded timers, captured log."""

    def __init__(self, cli, parser):
        super().__init__(cli=cli, parser=parser)
        self.logged = []
        self.started_workers = []
        self.intervals = []
        self.timers = []

    def append_log(self, renderable) -> None:
        self.logged.append(renderable)

    def run_worker(self, work, *args, **kwargs):
        self.started_workers.append(work)
        work()  # synchronous for tests

    def set_interval(self, interval, callback, **kwargs):
        self.intervals.append((interval, callback))

    def set_timer(self, delay, callback, **kwargs):
        self.timers.append((delay, callback))

    def call_from_thread(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)


def _make_app(monkeypatch) -> AutoSyncApp:
    for key in SPOTIFY_REQUIRED_KEYS:
        monkeypatch.setenv(key, "test_value")
    # Auto-sync only arms itself when a cached token exists (a missing token
    # would otherwise trigger the interactive OAuth flow from the worker).
    monkeypatch.setattr(
        interactive_app, "get_cached_token_info", lambda: {"access_token": "cached"}
    )
    app = AutoSyncApp(cli=PlaylistCLI(), parser=setup_parsers())
    app._refresh_env_status()
    app.cli.sync_listen_history = MagicMock()
    # Hermetic in-memory DB (the rollback path touches cli.repos.conn).
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    app.cli._repos = Repositories(conn)
    return app


class TestScheduling:
    def test_default_interval_is_30_minutes_plus_warmup(self, monkeypatch):
        monkeypatch.delenv("TUNR_AUTO_SYNC_MINUTES", raising=False)
        app = _make_app(monkeypatch)
        app._schedule_auto_sync()
        assert app.intervals == [(1800, app._maybe_auto_sync)]
        assert app.timers == [(3, app._maybe_auto_sync)]

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TUNR_AUTO_SYNC_MINUTES", "5")
        app = _make_app(monkeypatch)
        app._schedule_auto_sync()
        assert app.intervals == [(300, app._maybe_auto_sync)]

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("TUNR_AUTO_SYNC_MINUTES", "0")
        app = _make_app(monkeypatch)
        app._schedule_auto_sync()
        assert app.intervals == []
        assert app.timers == []

    def test_garbage_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("TUNR_AUTO_SYNC_MINUTES", "soon")
        app = _make_app(monkeypatch)
        app._schedule_auto_sync()
        assert app.intervals == [(1800, app._maybe_auto_sync)]


class TestMaybeAutoSync:
    def test_runs_quiet_sync_and_restores_idle(self, monkeypatch):
        app = _make_app(monkeypatch)
        seen_status = []
        app.cli.sync_listen_history.side_effect = lambda **kw: seen_status.append(app.status)

        app._maybe_auto_sync()

        app.cli.sync_listen_history.assert_called_once_with(quiet=True)
        # While the worker ran, the gate was held with the shared status field…
        assert seen_status == ["auto-sync"]
        # …and released afterwards.
        assert app.status == "idle"
        assert len(app.started_workers) == 1

    def test_skipped_when_a_command_is_running(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.status = "running /update"
        app._maybe_auto_sync()
        app.cli.sync_listen_history.assert_not_called()
        assert app.started_workers == []
        assert app.status == "running /update"

    def test_skipped_in_setup_mode(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._setup_mode = True
        app._maybe_auto_sync()
        app.cli.sync_listen_history.assert_not_called()

    def test_user_commands_blocked_while_auto_sync_holds_gate(self, monkeypatch):
        """_run_command refuses while auto-sync is mid-flight (same status gate)."""
        app = _make_app(monkeypatch)
        dispatched = []
        app.run_worker = lambda work, *a, **k: dispatched.append(work)  # don't run

        app._maybe_auto_sync()
        assert app.status == "auto-sync"

        app._run_command("stats", object())
        # Refused: no second worker dispatched, and the refusal line was logged.
        assert len(dispatched) == 1
        assert any("already running" in str(entry) for entry in app.logged)

    def test_finish_does_not_clobber_other_status(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.status = "running /update"
        app._finish_auto_sync()
        assert app.status == "running /update"

    def test_skipped_while_modal_screen_open(self, monkeypatch):
        """No sync while /dash is on the stack: the dashboard queries the
        shared sqlite connection synchronously on the app thread."""
        app = _make_app(monkeypatch)
        monkeypatch.setattr(
            type(app), "screen_stack", property(lambda self: ["default", "dashboard"])
        )
        app._maybe_auto_sync()
        app.cli.sync_listen_history.assert_not_called()
        assert app.started_workers == []
        assert app.status == "idle"

    def test_skipped_without_cached_token(self, monkeypatch, caplog):
        """Keys-present is not token-present: without a cached token the lazy
        SpotifyManager would launch the BLOCKING interactive OAuth flow from
        the worker thread (browser popup + permanently wedged status gate)."""
        app = _make_app(monkeypatch)
        monkeypatch.setattr(interactive_app, "get_cached_token_info", lambda: None)

        with caplog.at_level(logging.INFO, logger="interactive_app"):
            app._maybe_auto_sync()
            app._maybe_auto_sync()

        app.cli.sync_listen_history.assert_not_called()
        assert app.started_workers == []
        assert app.status == "idle"
        # Logged once, not every interval.
        skips = [r for r in caplog.records if "no cached Spotify token" in r.message]
        assert len(skips) == 1

    def test_runs_when_spotify_already_constructed(self, monkeypatch):
        """An already-authenticated session never re-checks the token cache."""
        app = _make_app(monkeypatch)
        monkeypatch.setattr(interactive_app, "get_cached_token_info", lambda: None)
        app.cli._spotify = object()  # SpotifyManager already constructed
        app._maybe_auto_sync()
        app.cli.sync_listen_history.assert_called_once_with(quiet=True)


class TestDashboardGate:
    def test_open_dashboard_refused_while_busy(self, monkeypatch):
        """/dash must not open while a worker is mid-write on the shared
        connection (it queries it synchronously on the app thread)."""
        app = _make_app(monkeypatch)
        pushed = []
        app.push_screen = lambda screen, callback=None: pushed.append(screen)

        app.status = "running /import-history"
        app._open_dashboard()
        assert pushed == []
        assert any("already running" in str(entry) for entry in app.logged)

    def test_open_dashboard_allowed_when_idle(self, monkeypatch):
        app = _make_app(monkeypatch)
        pushed = []
        app.push_screen = lambda screen, callback=None: pushed.append(screen)

        app._open_dashboard()
        assert len(pushed) == 1


class TestFailureRollback:
    def test_failure_mid_write_rolls_back_open_transaction(self, monkeypatch):
        """A sync that dies mid-write must not leave an open transaction that
        the next unrelated command's commit would silently persist."""
        app = _make_app(monkeypatch)

        def _fail_mid_write(**kwargs):
            app.cli.repos.conn.execute(
                "INSERT INTO sync_state (source, cursor, last_synced_at) "
                "VALUES ('partial', NULL, NULL);"
            )
            raise RuntimeError("api down mid-write")

        app.cli.sync_listen_history.side_effect = _fail_mid_write
        app._maybe_auto_sync()

        assert not app.cli.repos.conn.in_transaction  # rolled back, not dangling
        app.cli.repos.conn.commit()  # the "next unrelated command" commits
        assert app.cli.repos.sync_state.get("partial") is None
        assert app.status == "idle"


class TestFailureNoise:
    def test_failure_logged_once_then_quiet(self, monkeypatch, caplog):
        app = _make_app(monkeypatch)
        app.cli.sync_listen_history.side_effect = RuntimeError("api down")

        with caplog.at_level(logging.WARNING, logger="interactive_app"):
            app._maybe_auto_sync()
            app._maybe_auto_sync()

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
        assert app._auto_sync_warned is True
        # A generic failure keeps retrying — only scope errors stand down.
        assert app._auto_sync_scope_blocked is False
        # The gate is still released after every failure.
        assert app.status == "idle"
        assert app.cli.sync_listen_history.call_count == 2


class TestScopeBlock:
    """A 403 insufficient-scope failure can never succeed on retry: log the
    actionable hint once, stop burning the busy slot, and resume only when a
    cached token granting every required scope appears."""

    def test_scope_failure_logs_hint_once_and_stops_retrying(self, monkeypatch, caplog):
        app = _make_app(monkeypatch)
        app.cli.sync_listen_history.side_effect = _ScopeError()

        with caplog.at_level(logging.WARNING, logger="interactive_app"):
            app._maybe_auto_sync()  # fails on scope -> hint + blocked
            app._maybe_auto_sync()  # blocked: no attempt, no new log
            app._maybe_auto_sync()

        assert app.cli.sync_listen_history.call_count == 1
        assert len(app.started_workers) == 1  # busy slot burned exactly once
        assert app._auto_sync_scope_blocked is True
        assert app.status == "idle"  # gate released after the failure
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
        assert "/auth-reset" in warnings[0].getMessage()  # the actionable hint

    def test_block_survives_a_token_that_still_lacks_scopes(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.cli.sync_listen_history.side_effect = _ScopeError()
        app._maybe_auto_sync()
        assert app._auto_sync_scope_blocked is True

        # A cached token exists but still lacks the listening scopes (the user
        # has not re-authorized yet): stay blocked.
        monkeypatch.setattr(
            interactive_app,
            "get_cached_token_info",
            lambda: {"access_token": "stale", "scope": "playlist-read-private"},
        )
        app._maybe_auto_sync()
        assert app._auto_sync_scope_blocked is True
        assert app.cli.sync_listen_history.call_count == 1

    def test_block_self_clears_when_full_scope_token_appears(self, monkeypatch, caplog):
        """The exact re-auth sequence: blocked -> /auth-reset --yes + consent
        writes a full-scope token -> the next tick resumes syncing."""
        app = _make_app(monkeypatch)
        app.cli.sync_listen_history.side_effect = _ScopeError()
        app._maybe_auto_sync()
        assert app._auto_sync_scope_blocked is True

        # /auth-reset --yes deleted the token; nothing cached yet: stay blocked.
        monkeypatch.setattr(interactive_app, "get_cached_token_info", lambda: None)
        app._maybe_auto_sync()
        assert app.cli.sync_listen_history.call_count == 1

        # The user re-authorized: a cached token with every required scope.
        app.cli.sync_listen_history.side_effect = None
        monkeypatch.setattr(
            interactive_app,
            "get_cached_token_info",
            lambda: {"access_token": "fresh", "scope": " ".join(SPOTIFY_SCOPES)},
        )
        with caplog.at_level(logging.INFO, logger="interactive_app"):
            app._maybe_auto_sync()

        assert app._auto_sync_scope_blocked is False
        assert app.cli.sync_listen_history.call_count == 2
        assert app.status == "idle"
        assert any("resuming" in r.message for r in caplog.records)
