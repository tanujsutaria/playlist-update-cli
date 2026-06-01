"""Unit tests for PlaylistCLI.undo_last_write — the session-scoped /undo.

A playlist write snapshots the playlist's prior tracks; /undo restores them via
the ID-preserving replace (never delete+recreate). Offline; the Spotify client
and store are mocked, only the repos are real.
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
    cli._undo_stack = []
    return cli


class TestUndoLastWrite:
    def test_nothing_to_undo(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        assert cli.undo_last_write() is False
        assert "Nothing to undo" in capsys.readouterr().out
        cli._spotify.replace_playlist_items.assert_not_called()

    def test_restores_prior_tracks_via_replace(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        # The playlist had two tracks before the last write.
        prior = [
            {"name": "Old One", "artist": "Artist A", "uri": "spotify:track:1"},
            {"name": "Old Two", "artist": "Artist B", "uri": "spotify:track:2"},
        ]
        cli._spotify.get_playlist_tracks.return_value = prior
        cli._spotify.append_to_playlist.return_value = True
        cli._spotify.replace_playlist_items.return_value = True

        cli.add_search_to_playlist("My Mix", ["wild nothing|||a"])  # records undo
        assert len(cli._undo_stack) == 1

        assert cli.undo_last_write() is True
        cli._spotify.replace_playlist_items.assert_called_once()
        name, songs = cli._spotify.replace_playlist_items.call_args[0]
        assert name == "My Mix"
        # Restored Songs carry the prior URIs (so no re-search is needed).
        assert [s.spotify_uri for s in songs] == ["spotify:track:1", "spotify:track:2"]
        assert [s.name for s in songs] == ["Old One", "Old Two"]
        assert cli._undo_stack == []  # popped after a successful restore
        assert "Restored" in capsys.readouterr().out

    def test_undo_of_new_playlist_clears_it(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        cli._spotify.get_playlist_tracks.return_value = []  # playlist didn't exist before
        cli._spotify.append_to_playlist.return_value = True
        cli._spotify.replace_playlist_items.return_value = True

        cli.add_search_to_playlist("Brand New", ["wild nothing|||a"])
        assert cli.undo_last_write() is True
        name, songs = cli._spotify.replace_playlist_items.call_args[0]
        assert name == "Brand New"
        assert songs == []  # restoring to empty == clearing what we added
        assert "Cleared" in capsys.readouterr().out

    def test_failed_undo_keeps_snapshot_for_retry(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        cli._spotify.get_playlist_tracks.return_value = [
            {"name": "Old", "artist": "A", "uri": "spotify:track:1"}
        ]
        cli._spotify.append_to_playlist.return_value = True
        cli._spotify.replace_playlist_items.return_value = False  # undo write fails

        cli.add_search_to_playlist("My Mix", ["wild nothing|||a"])
        assert cli.undo_last_write() is False
        assert len(cli._undo_stack) == 1  # snapshot retained so the user can retry
        assert "Failed to undo" in capsys.readouterr().out

    def test_undo_is_lifo(self, tmp_path):
        cli = _cli(tmp_path)
        cli._spotify.get_playlist_tracks.return_value = []
        cli._spotify.append_to_playlist.return_value = True
        cli._spotify.replace_playlist_items.return_value = True

        cli.add_search_to_playlist("First", ["wild nothing|||a"])
        cli.add_search_to_playlist("Second", ["wild nothing|||b"])
        cli.undo_last_write()
        # Most recent write is undone first.
        assert cli._spotify.replace_playlist_items.call_args[0][0] == "Second"
        cli.undo_last_write()
        assert cli._spotify.replace_playlist_items.call_args[0][0] == "First"

    def test_undo_never_uses_destructive_refresh(self, tmp_path):
        cli = _cli(tmp_path)
        cli._spotify.get_playlist_tracks.return_value = [
            {"name": "Old", "artist": "A", "uri": "spotify:track:1"}
        ]
        cli._spotify.append_to_playlist.return_value = True
        cli._spotify.replace_playlist_items.return_value = True
        cli.add_search_to_playlist("My Mix", ["wild nothing|||a"])
        cli.undo_last_write()
        cli._spotify.refresh_playlist.assert_not_called()
