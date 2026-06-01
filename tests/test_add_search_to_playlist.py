"""Unit tests for PlaylistCLI.add_search_to_playlist — the guarded write helper
behind /search --to NAME.

Verifies that cached track IDs resolve to Songs and that append (the safe
default) vs replace (--replace, ID-preserving) route to the right Spotify call,
and that the destructive delete-and-recreate path is never taken. Offline; the
Spotify client and the store are mocked, only the repos are real.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import ui
from main import PlaylistCLI
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories


@pytest.fixture(autouse=True)
def _no_sink():
    ui.set_output_sink(None)
    yield
    ui.set_output_sink(None)


def _cli(tmp_path):
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    conn.execute("INSERT INTO artists (artist_id, name) VALUES ('wild nothing', 'Wild Nothing')")
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, ?, 'candidate')",
        [
            ("wild nothing|||a", "Alpha", "wild nothing"),
            ("wild nothing|||b", "Beta", "wild nothing"),
        ],
    )
    conn.commit()
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    cli._spotify = MagicMock()
    cli._db = MagicMock()
    return cli


class TestAddSearchToPlaylist:
    def test_append_is_the_default(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        cli._spotify.append_to_playlist.return_value = True
        ok = cli.add_search_to_playlist("My Mix", ["wild nothing|||a", "wild nothing|||b"])
        assert ok is True
        cli._spotify.append_to_playlist.assert_called_once()
        cli._spotify.replace_playlist_items.assert_not_called()
        name, songs = cli._spotify.append_to_playlist.call_args[0]
        assert name == "My Mix"
        # Track IDs resolved to Songs, with the artist's display name attached.
        assert [s.name for s in songs] == ["Alpha", "Beta"]
        assert {s.artist for s in songs} == {"Wild Nothing"}
        cli._db._save_state.assert_called_once()

    def test_replace_routes_to_replace_items(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        cli._spotify.replace_playlist_items.return_value = True
        ok = cli.add_search_to_playlist("My Mix", ["wild nothing|||a"], replace=True)
        assert ok is True
        cli._spotify.replace_playlist_items.assert_called_once()
        cli._spotify.append_to_playlist.assert_not_called()
        # The user is told the playlist itself is preserved.
        assert "Swapping" in capsys.readouterr().out

    def test_empty_track_ids_is_a_noop(self, tmp_path):
        cli = _cli(tmp_path)
        assert cli.add_search_to_playlist("Mix", []) is False
        cli._spotify.append_to_playlist.assert_not_called()
        cli._spotify.replace_playlist_items.assert_not_called()

    def test_unresolvable_ids_do_not_write(self, tmp_path):
        cli = _cli(tmp_path)
        assert cli.add_search_to_playlist("Mix", ["ghost|||track"]) is False
        cli._spotify.append_to_playlist.assert_not_called()

    def test_failed_write_returns_false_and_skips_save(self, tmp_path):
        cli = _cli(tmp_path)
        cli._spotify.append_to_playlist.return_value = False
        assert cli.add_search_to_playlist("Mix", ["wild nothing|||a"]) is False
        cli._db._save_state.assert_not_called()

    def test_never_uses_destructive_refresh(self, tmp_path):
        """Safety invariant: a typo'd NAME can never delete+recreate a playlist."""
        cli = _cli(tmp_path)
        cli._spotify.append_to_playlist.return_value = True
        cli._spotify.replace_playlist_items.return_value = True
        cli.add_search_to_playlist("Mix", ["wild nothing|||a"], replace=False)
        cli.add_search_to_playlist("Mix", ["wild nothing|||a"], replace=True)
        cli._spotify.refresh_playlist.assert_not_called()
