"""
Unit tests for auth-status and auth-refresh commands.
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import main


@pytest.fixture
def cli_no_init():
    cli = main.PlaylistCLI.__new__(main.PlaylistCLI)
    cli._db = None
    cli._spotify = None
    cli._rotation_managers = {}
    return cli


def test_auth_status_no_token(monkeypatch, caplog, cli_no_init):
    monkeypatch.setattr(main, "get_cached_token_info", lambda: None)
    caplog.set_level("INFO", logger=main.__name__)

    cli_no_init.auth_status()

    assert "No cached Spotify token found." in caplog.text


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


def test_auth_refresh_no_token(monkeypatch, caplog, cli_no_init):
    monkeypatch.setattr(main, "refresh_cached_token", lambda: None)
    caplog.set_level("INFO", logger=main.__name__)

    cli_no_init.auth_refresh()

    assert "No token refreshed" in caplog.text


def test_auth_refresh_with_expiry(monkeypatch, caplog, cli_no_init):
    ts = 1_700_000_000
    monkeypatch.setattr(main, "refresh_cached_token", lambda: {"expires_at": ts})
    caplog.set_level("INFO", logger=main.__name__)

    cli_no_init.auth_refresh()

    assert "Token refreshed" in caplog.text
