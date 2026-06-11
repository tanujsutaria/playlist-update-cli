"""Tests for the Spotify listening-scope fix + the friendly insufficient-scope hint.

Covers: the requested OAuth scopes now include the listening scopes, the
scope_error_hint() detector, and that /listen-sync and /ingest surface the
actionable re-auth hint (instead of a raw 403 traceback) on a scope error.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

import ui
from main import PlaylistCLI
from spotify_manager import SCOPE_REAUTH_HINT, SPOTIFY_SCOPES, scope_error_hint
from storage.migrations import ensure_schema
from storage.repos import Repositories


class _FakeSpotifyException(Exception):
    """Mimics spotipy.SpotifyException: carries an http_status attribute."""

    def __init__(self, http_status: int, message: str) -> None:
        super().__init__(message)
        self.http_status = http_status


@pytest.fixture(autouse=True)
def _no_sink():
    ui.set_output_sink(None)
    yield
    ui.set_output_sink(None)


def _cli_with_spotify(side_effect) -> PlaylistCLI:
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._spotify = MagicMock()
    cli._spotify.sp.current_user_recently_played.side_effect = side_effect
    cli._spotify.sp.current_user_top_tracks.side_effect = side_effect
    cli._spotify.get_playlist_tracks.side_effect = side_effect
    # The cursor-based listen sync reads sync_state BEFORE the API call, so
    # the CLI needs a real (in-memory, offline) repos — never data/tunr.db.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    cli._repos = Repositories(conn)
    return cli


class TestScopes:
    def test_listening_scopes_present(self):
        assert "user-read-recently-played" in SPOTIFY_SCOPES
        assert "user-top-read" in SPOTIFY_SCOPES

    def test_original_scopes_preserved(self):
        for scope in (
            "playlist-modify-public",
            "playlist-modify-private",
            "playlist-read-private",
            "user-library-read",
        ):
            assert scope in SPOTIFY_SCOPES


class TestScopeErrorHint:
    def test_403_with_scope_message_returns_hint(self):
        exc = _FakeSpotifyException(403, "http status: 403, code:-1 - Insufficient client scope")
        assert scope_error_hint(exc) == SCOPE_REAUTH_HINT

    def test_403_without_scope_message_returns_none(self):
        exc = _FakeSpotifyException(403, "forbidden: premium required")
        assert scope_error_hint(exc) is None

    def test_429_returns_none(self):
        exc = _FakeSpotifyException(429, "rate limited; scope")  # status gates it out
        assert scope_error_hint(exc) is None

    def test_plain_exception_returns_none(self):
        assert scope_error_hint(RuntimeError("network down")) is None


class TestListenSyncErrorSurface:
    def test_scope_error_surfaces_reauth_hint(self, capsys):
        cli = _cli_with_spotify(_FakeSpotifyException(403, "403 ... Insufficient client scope"))
        cli.sync_listen_history(50)
        out = capsys.readouterr().out
        assert "Re-authorize" in out
        assert "consent" in out
        assert "Listen sync failed" not in out  # generic message suppressed

    def test_non_scope_error_keeps_generic_message(self, capsys):
        cli = _cli_with_spotify(RuntimeError("network down"))
        cli.sync_listen_history(50)
        out = capsys.readouterr().out
        assert "Listen sync failed" in out
        assert "network down" in out


class TestIngestErrorSurface:
    def test_top_scope_error_surfaces_reauth_hint(self, capsys):
        cli = _cli_with_spotify(_FakeSpotifyException(403, "403 ... Insufficient client scope"))
        cli.ingest_tracks("top", None, "medium_term")
        out = capsys.readouterr().out
        assert "Re-authorize" in out
        assert "Ingest failed" not in out
