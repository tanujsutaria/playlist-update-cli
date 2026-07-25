"""Visibility of the blocking OAuth consent wait (scrollback + status bar).

Regression tests for the frozen-/pull incident: with no valid cached token,
spotipy's interactive flow opens a browser and blocks the worker thread with
zero in-app feedback. SpotifyManager must announce the wait (including the
actual SPOTIFY_REDIRECT_URI) BEFORE the flow can start, set the status-bar
stage channel, and confirm + clear once the flow returns. A valid cached
token must emit none of this.

All Spotify / OAuth machinery is monkeypatched — offline, no real
credentials, and nothing touches .spotify_cache/.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import spotify_manager
import ui
from spotify_manager import CONSENT_WAIT_STAGE, SpotifyManager

REDIRECT_URI = "http://127.0.0.1:9090/callback"


@pytest.fixture
def sinks():
    """Install capture sinks for scrollback + status; always reset after.

    The ui sinks are process-global — every test that installs one must
    clear them afterwards or unrelated tests flake across ordering (same
    contract as tests/test_ui.py's reset_sinks).
    """
    ui.set_output_sink(None)
    ui.set_preview_sink(None)
    ui.set_status_sink(None)
    captured = {"lines": [], "status": []}
    ui.set_output_sink(lambda renderable: captured["lines"].append(renderable))
    ui.set_status_sink(lambda stage: captured["status"].append(stage))
    yield captured
    ui.set_output_sink(None)
    ui.set_preview_sink(None)
    ui.set_status_sink(None)


@pytest.fixture
def spotify_stub(monkeypatch):
    """Stub every network/browser-touching seam of SpotifyManager.__init__.

    _get_auth_manager is replaced whole (it would otherwise build a real
    SpotifyOAuth and touch the token cache dir); the spotipy client is a
    MagicMock whose current_user/current_user_playlists succeed.
    """
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", REDIRECT_URI)
    monkeypatch.setattr(spotify_manager, "_get_auth_manager", lambda open_browser=True: MagicMock())
    client = MagicMock()
    client.current_user.return_value = {"id": "test_user"}
    client.current_user_playlists.return_value = {"items": [], "next": None}
    monkeypatch.setattr(spotify_manager.spotipy, "Spotify", lambda auth_manager: client)
    return client


def _plain_lines(captured):
    return [getattr(renderable, "plain", str(renderable)) for renderable in captured["lines"]]


class TestNoCachedToken:
    def test_consent_wait_message_names_the_redirect_uri(self, monkeypatch, sinks, spotify_stub):
        monkeypatch.setattr(spotify_manager, "get_cached_token_info", lambda: None)

        SpotifyManager()

        lines = _plain_lines(sinks)
        consent_lines = [line for line in lines if "Waiting for Spotify consent" in line]
        assert len(consent_lines) == 1
        assert REDIRECT_URI in consent_lines[0]
        assert "/auth-status" in consent_lines[0]
        assert "Ctrl+C" in consent_lines[0]

    def test_success_confirms_token_cached(self, monkeypatch, sinks, spotify_stub):
        monkeypatch.setattr(spotify_manager, "get_cached_token_info", lambda: None)

        SpotifyManager()

        lines = _plain_lines(sinks)
        assert any("token cached" in line for line in lines)

    def test_status_stage_mentions_consent_then_clears(self, monkeypatch, sinks, spotify_stub):
        monkeypatch.setattr(spotify_manager, "get_cached_token_info", lambda: None)

        SpotifyManager()

        stages = sinks["status"]
        assert stages[0] == CONSENT_WAIT_STAGE
        assert "consent" in stages[0]
        assert stages[-1] is None  # cleared once the flow returned

    def test_auth_failure_still_clears_status_and_propagates(
        self, monkeypatch, sinks, spotify_stub
    ):
        monkeypatch.setattr(spotify_manager, "get_cached_token_info", lambda: None)
        spotify_stub.current_user.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            SpotifyManager()

        assert sinks["status"] == [CONSENT_WAIT_STAGE, None]


class TestValidCachedToken:
    def test_no_consent_messaging_on_normal_commands(self, monkeypatch, sinks, spotify_stub):
        monkeypatch.setattr(
            spotify_manager,
            "get_cached_token_info",
            lambda: {"access_token": "tok", "expires_in": 3600},
        )

        SpotifyManager()

        lines = _plain_lines(sinks)
        assert lines == []  # zero scrollback noise when the token is valid
        assert sinks["status"] == []  # and the status channel stays untouched
