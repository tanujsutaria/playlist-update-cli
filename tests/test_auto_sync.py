"""Tests for the TUI's background listen-sync (quiet, cursor-based).

The auto-sync must take the SAME `status` gate `_run_command` checks (single
shared sqlite connection), run sync_listen_history(quiet=True) on a worker,
log failures exactly once, and always release the gate. Scheduling honours
TUNR_AUTO_SYNC_MINUTES (0 disables). All offline; timers/workers are stubbed.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from arg_parse import setup_parsers
from interactive_app import SPOTIFY_REQUIRED_KEYS, PlaylistInteractiveApp
from main import PlaylistCLI


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
    app = AutoSyncApp(cli=PlaylistCLI(), parser=setup_parsers())
    app._refresh_env_status()
    app.cli.sync_listen_history = MagicMock()
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
        # The gate is still released after every failure.
        assert app.status == "idle"
        assert app.cli.sync_listen_history.call_count == 2
