"""
Unit tests for auth-status, auth-refresh, and auth-reset commands, plus the
spotify_manager token-reset/scope-diff helpers behind them.
"""

from datetime import datetime

import pytest

import main
import spotify_manager
from spotify_manager import SPOTIFY_SCOPES


@pytest.fixture
def cli_no_init():
    cli = main.PlaylistCLI.__new__(main.PlaylistCLI)
    cli._db = None
    cli._spotify = None
    cli._rotation_managers = {}
    return cli


def test_auth_status_no_token(monkeypatch, cli_no_init):
    """auth_status with no cached token should display a message via UI info()."""
    monkeypatch.setattr(main, "get_cached_token_info", lambda: None)
    calls = []
    monkeypatch.setattr(main, "info", lambda msg: calls.append(msg))

    cli_no_init.auth_status()

    assert any("No cached Spotify token found" in c for c in calls)


def test_auth_status_with_token(monkeypatch, cli_no_init):
    ts = 1_700_000_000
    token_info = {"expires_at": ts, "expires_in": 3600, "scope": "playlist-read-private"}
    monkeypatch.setattr(main, "get_cached_token_info", lambda: token_info)

    rows_holder = {}

    def fake_section(*args, **kwargs):
        return None

    def fake_key_value_table(rows):
        rows_holder["rows"] = rows

    monkeypatch.setattr(main, "section", fake_section)
    monkeypatch.setattr(main, "key_value_table", fake_key_value_table)

    cli_no_init.auth_status()

    rows = rows_holder.get("rows", [])
    expected_expires = datetime.fromtimestamp(ts).isoformat()
    assert ["Expires at", expected_expires] in rows
    assert ["Expires in (seconds)", 3600] in rows
    assert ["Scopes", "playlist-read-private"] in rows


def _auth_status_rows(monkeypatch, cli, token_info):
    """Run auth_status against a fake cached token, capturing the table rows."""
    monkeypatch.setattr(main, "get_cached_token_info", lambda: token_info)
    rows_holder = {}
    monkeypatch.setattr(main, "section", lambda *a, **k: None)
    monkeypatch.setattr(main, "key_value_table", lambda rows: rows_holder.update(rows=rows))
    cli.auth_status()
    return rows_holder.get("rows", [])


def _verdict(rows):
    verdicts = [value for label, value in rows if label == "Verdict"]
    assert len(verdicts) == 1
    return verdicts[0]


def test_auth_status_verdict_full_scopes(monkeypatch, cli_no_init):
    """A token granting every required scope gets a clean verdict."""
    token_info = {"expires_in": 3600, "scope": " ".join(SPOTIFY_SCOPES)}
    rows = _auth_status_rows(monkeypatch, cli_no_init, token_info)
    verdict = _verdict(rows)
    assert "missing scopes" not in verdict
    assert "all required scopes" in verdict


def test_auth_status_verdict_missing_scopes(monkeypatch, cli_no_init):
    """A stale token missing the listening scopes names them + /auth-reset."""
    granted = [s for s in SPOTIFY_SCOPES if not s.startswith("user-")]
    token_info = {"expires_in": 3600, "scope": " ".join(granted)}
    rows = _auth_status_rows(monkeypatch, cli_no_init, token_info)
    verdict = _verdict(rows)
    assert verdict.startswith("missing scopes: ")
    assert "user-read-recently-played" in verdict
    assert "user-top-read" in verdict
    assert "run /auth-reset" in verdict


def test_auth_status_verdict_token_without_scope_field(monkeypatch, cli_no_init):
    """A token whose scope field is absent counts as missing everything."""
    rows = _auth_status_rows(monkeypatch, cli_no_init, {"expires_in": 3600})
    verdict = _verdict(rows)
    assert "missing scopes" in verdict
    for scope in SPOTIFY_SCOPES:
        assert scope in verdict


def test_auth_status_no_token_still_short_circuits(monkeypatch, cli_no_init):
    """No cached token: no verdict row is fabricated, just the info line."""
    monkeypatch.setattr(main, "get_cached_token_info", lambda: None)
    calls = []
    monkeypatch.setattr(main, "info", lambda msg: calls.append(msg))
    tables = []
    monkeypatch.setattr(main, "key_value_table", lambda rows: tables.append(rows))

    cli_no_init.auth_status()

    assert any("No cached Spotify token found" in c for c in calls)
    assert tables == []


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
    def test_bare_reset_warns_and_deletes_nothing(self, monkeypatch, cli_no_init):
        deleted = []
        monkeypatch.setattr(main, "reset_cached_token", lambda: deleted.append(True) or True)
        warnings = []
        monkeypatch.setattr(main, "warning", lambda msg: warnings.append(msg))
        sentinel = object()
        cli_no_init._spotify = sentinel

        cli_no_init.auth_reset()

        assert deleted == []
        assert cli_no_init._spotify is sentinel  # client untouched without --yes
        assert any("--yes" in w for w in warnings)

    def test_yes_deletes_token_and_drops_client(self, monkeypatch, cli_no_init):
        deleted = []
        monkeypatch.setattr(main, "reset_cached_token", lambda: deleted.append(True) or True)
        infos = []
        monkeypatch.setattr(main, "info", lambda msg: infos.append(msg))
        cli_no_init._spotify = object()  # live client with the old token

        cli_no_init.auth_reset(yes=True)

        assert deleted == [True]
        # The in-memory client must be dropped too: a live client keeps using
        # its old token until expiry, so "next command re-opens consent" would
        # otherwise be false.
        assert cli_no_init._spotify is None
        assert any("consent" in msg for msg in infos)

    def test_yes_with_no_token_is_graceful(self, monkeypatch, cli_no_init):
        monkeypatch.setattr(main, "reset_cached_token", lambda: False)
        infos = []
        monkeypatch.setattr(main, "info", lambda msg: infos.append(msg))

        cli_no_init.auth_reset(yes=True)

        assert cli_no_init._spotify is None
        assert any("No cached Spotify token" in msg for msg in infos)


def test_auth_refresh_no_token(monkeypatch, cli_no_init):
    """auth_refresh with no token should display a warning via UI warning()."""
    monkeypatch.setattr(main, "refresh_cached_token", lambda: None)
    calls = []
    monkeypatch.setattr(main, "warning", lambda msg: calls.append(msg))

    cli_no_init.auth_refresh()

    assert any("No token refreshed" in c for c in calls)


def test_auth_refresh_with_expiry(monkeypatch, cli_no_init):
    """auth_refresh with new expiry should display a message via UI info()."""
    ts = 1_700_000_000
    monkeypatch.setattr(main, "refresh_cached_token", lambda: {"expires_at": ts})
    calls = []
    monkeypatch.setattr(main, "info", lambda msg: calls.append(msg))

    cli_no_init.auth_refresh()

    assert any("Token refreshed" in c for c in calls)


def test_auth_refresh_without_expiry(monkeypatch, cli_no_init):
    """auth_refresh with no expiry data should still display a message."""
    monkeypatch.setattr(main, "refresh_cached_token", lambda: {"some_key": "val"})
    calls = []
    monkeypatch.setattr(main, "info", lambda msg: calls.append(msg))

    cli_no_init.auth_refresh()

    assert any("Token refreshed" in c for c in calls)


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
