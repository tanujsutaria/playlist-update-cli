"""
Unit tests for auth-status, auth-refresh, and auth-reset commands, plus the
spotify_manager token-reset/scope-diff helpers behind them.

Token functions are patched on ``spotify_manager`` (the owning module — main
calls them qualified, so the seam survives the command bodies moving out of
main.py); rendered output is captured via the ``ui.set_output_sink`` choke
point instead of patching main's from-imported ui names.
"""

from datetime import datetime
from io import StringIO

import pytest
from rich.console import Console

import main
import spotify_manager
import ui
from spotify_manager import SPOTIFY_SCOPES


@pytest.fixture
def cli_no_init():
    cli = main.PlaylistCLI.__new__(main.PlaylistCLI)
    cli._db = None
    cli._spotify = None
    cli._rotation_managers = {}
    return cli


@pytest.fixture
def sink():
    captured = []
    ui.set_output_sink(captured.append)
    yield captured
    ui.set_output_sink(None)


def _rendered(captured, width: int = 400) -> str:
    """Render captured output wide enough that no table cell ever wraps —
    scope names are hyphenated, and a wrap mid-name would break substring
    assertions."""
    buf = StringIO()
    console = Console(file=buf, width=width)
    for renderable in captured:
        console.print(renderable)
    return buf.getvalue()


def test_auth_status_no_token(monkeypatch, cli_no_init, sink):
    """auth_status with no cached token should display a message via UI info()."""
    monkeypatch.setattr(spotify_manager, "get_cached_token_info", lambda: None)

    cli_no_init.auth_status()

    assert "No cached Spotify token found" in _rendered(sink)


def test_auth_status_with_token(monkeypatch, cli_no_init, sink):
    ts = 1_700_000_000
    token_info = {"expires_at": ts, "expires_in": 3600, "scope": "playlist-read-private"}
    monkeypatch.setattr(spotify_manager, "get_cached_token_info", lambda: token_info)

    cli_no_init.auth_status()

    out = _rendered(sink)
    expected_expires = datetime.fromtimestamp(ts).isoformat()
    assert "Expires at" in out
    assert expected_expires in out
    assert "Expires in (seconds)" in out
    assert "3600" in out
    assert "Scopes" in out
    assert "playlist-read-private" in out


def _auth_status_output(monkeypatch, cli, sink, token_info):
    """Run auth_status against a fake cached token, returning rendered output."""
    monkeypatch.setattr(spotify_manager, "get_cached_token_info", lambda: token_info)
    cli.auth_status()
    return _rendered(sink)


def test_auth_status_verdict_full_scopes(monkeypatch, cli_no_init, sink):
    """A token granting every required scope gets a clean verdict."""
    token_info = {"expires_in": 3600, "scope": " ".join(SPOTIFY_SCOPES)}
    out = _auth_status_output(monkeypatch, cli_no_init, sink, token_info)
    assert "Verdict" in out
    assert "missing scopes" not in out
    assert "all required scopes" in out


def test_auth_status_verdict_missing_scopes(monkeypatch, cli_no_init, sink):
    """A stale token missing the listening scopes names them + /auth-reset."""
    granted = [s for s in SPOTIFY_SCOPES if not s.startswith("user-")]
    token_info = {"expires_in": 3600, "scope": " ".join(granted)}
    out = _auth_status_output(monkeypatch, cli_no_init, sink, token_info)
    assert "missing scopes: " in out
    assert "user-read-recently-played" in out
    assert "user-top-read" in out
    assert "run /auth-reset" in out


def test_auth_status_verdict_token_without_scope_field(monkeypatch, cli_no_init, sink):
    """A token whose scope field is absent counts as missing everything."""
    out = _auth_status_output(monkeypatch, cli_no_init, sink, {"expires_in": 3600})
    assert "missing scopes" in out
    for scope in SPOTIFY_SCOPES:
        assert scope in out


def test_auth_status_no_token_still_short_circuits(monkeypatch, cli_no_init, sink):
    """No cached token: no verdict table is fabricated, just the info line."""
    monkeypatch.setattr(spotify_manager, "get_cached_token_info", lambda: None)

    cli_no_init.auth_status()

    out = _rendered(sink)
    assert "No cached Spotify token found" in out
    assert "Verdict" not in out
    assert "Expires" not in out


class TestMissingScopes:
    def test_full_scope_string_has_nothing_missing(self):
        assert spotify_manager.missing_scopes(" ".join(SPOTIFY_SCOPES)) == []

    def test_partial_scope_string_reports_the_gap(self):
        missing = spotify_manager.missing_scopes("playlist-read-private user-library-read")
        assert "user-read-recently-played" in missing
        assert "user-top-read" in missing
        assert "user-library-read" not in missing

    def test_none_and_empty_mean_everything_missing(self):
        assert spotify_manager.missing_scopes(None) == list(SPOTIFY_SCOPES)
        assert spotify_manager.missing_scopes("") == list(SPOTIFY_SCOPES)

    def test_extra_granted_scopes_are_fine(self):
        scope = " ".join(SPOTIFY_SCOPES) + " user-read-email"
        assert spotify_manager.missing_scopes(scope) == []


class TestResetCachedToken:
    """The reset helper deletes by path only — token contents are never read."""

    def test_deletes_existing_token_file(self, monkeypatch, tmp_path):
        token_path = tmp_path / ".spotify_token"
        token_path.write_text("opaque")
        monkeypatch.setattr(spotify_manager, "_token_cache_path", lambda: token_path)

        assert spotify_manager.reset_cached_token() is True
        assert not token_path.exists()

    def test_no_file_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            spotify_manager, "_token_cache_path", lambda: tmp_path / ".spotify_token"
        )
        assert spotify_manager.reset_cached_token() is False


class TestAuthReset:
    def test_bare_reset_warns_and_deletes_nothing(self, monkeypatch, cli_no_init, sink):
        deleted = []
        monkeypatch.setattr(
            spotify_manager, "reset_cached_token", lambda: deleted.append(True) or True
        )
        sentinel = object()
        cli_no_init._spotify = sentinel

        cli_no_init.auth_reset()

        assert deleted == []
        assert cli_no_init._spotify is sentinel  # client untouched without --yes
        assert "--yes" in _rendered(sink)

    def test_yes_deletes_token_and_drops_client(self, monkeypatch, cli_no_init, sink):
        deleted = []
        monkeypatch.setattr(
            spotify_manager, "reset_cached_token", lambda: deleted.append(True) or True
        )
        cli_no_init._spotify = object()  # live client with the old token

        cli_no_init.auth_reset(yes=True)

        assert deleted == [True]
        # The in-memory client must be dropped too: a live client keeps using
        # its old token until expiry, so "next command re-opens consent" would
        # otherwise be false.
        assert cli_no_init._spotify is None
        assert "consent" in _rendered(sink)

    def test_yes_with_no_token_is_graceful(self, monkeypatch, cli_no_init, sink):
        monkeypatch.setattr(spotify_manager, "reset_cached_token", lambda: False)

        cli_no_init.auth_reset(yes=True)

        assert cli_no_init._spotify is None
        assert "No cached Spotify token" in _rendered(sink)


def test_auth_refresh_no_token(monkeypatch, cli_no_init, sink):
    """auth_refresh with no token should display a warning via UI warning()."""
    monkeypatch.setattr(spotify_manager, "refresh_cached_token", lambda: None)

    cli_no_init.auth_refresh()

    assert "No token refreshed" in _rendered(sink)


def test_auth_refresh_with_expiry(monkeypatch, cli_no_init, sink):
    """auth_refresh with new expiry should display a message via UI info()."""
    ts = 1_700_000_000
    monkeypatch.setattr(spotify_manager, "refresh_cached_token", lambda: {"expires_at": ts})

    cli_no_init.auth_refresh()

    assert "Token refreshed" in _rendered(sink)


def test_auth_refresh_without_expiry(monkeypatch, cli_no_init, sink):
    """auth_refresh with no expiry data should still display a message."""
    monkeypatch.setattr(spotify_manager, "refresh_cached_token", lambda: {"some_key": "val"})

    cli_no_init.auth_refresh()

    assert "Token refreshed" in _rendered(sink)


def test_auth_manager_warns_on_localhost_redirect_uri(monkeypatch, caplog):
    """A localhost redirect URI gets a warning naming the 127.0.0.1 fix.

    Spotify rejects `localhost` at the consent screen with a browser-side
    "redirect_uri: Insecure" error and no terminal hint; the warning is the
    only in-app pointer to the fix.
    """
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
    monkeypatch.setattr(spotify_manager, "SpotifyOAuth", lambda **kwargs: object())
    monkeypatch.setattr(spotify_manager, "_get_cache_handler", lambda: None)

    with caplog.at_level("WARNING", logger="spotify_manager"):
        spotify_manager._get_auth_manager()

    assert any("127.0.0.1" in r.message for r in caplog.records)


def test_auth_manager_accepts_loopback_ip_redirect_uri(monkeypatch, caplog):
    """The loopback IP literal form does not trigger the localhost warning."""
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    monkeypatch.setattr(spotify_manager, "SpotifyOAuth", lambda **kwargs: object())
    monkeypatch.setattr(spotify_manager, "_get_cache_handler", lambda: None)

    with caplog.at_level("WARNING", logger="spotify_manager"):
        spotify_manager._get_auth_manager()

    assert not any("Insecure" in r.message for r in caplog.records)
