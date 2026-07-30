"""Behavior tests for the quick track ops: /add, /remove, /move.

One-line single-track playlist edits, fuzzy-resolved against the local tracks
mirror, with full /undo support and a playlist_tracks mirror patch after every
successful Spotify write. Offline: the Spotify client is mocked, the repos are
real (throwaway SQLite).
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


# What the mocked Spotify returns for pre-write snapshots (per playlist).
SNAPSHOTS = {
    "My Mix": [
        {"name": "Shadow", "artist": "Wild Nothing", "uri": "spotify:track:shadow1"},
        {"name": "Daydream", "artist": "Beach Fossils", "uri": "spotify:track:day1"},
    ],
    "Chill": [
        {"name": "Chinatown", "artist": "Wild Nothing", "uri": "spotify:track:china1"},
    ],
}


def _cli(tmp_path):
    """PlaylistCLI over a seeded throwaway mirror + a mocked Spotify client.

    Seeds five mirrored tracks (one URI-less pre-capture row and a same-name
    pair for ambiguity tests) and two mirrored playlists with memberships.
    """
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    conn.executemany(
        "INSERT INTO artists (artist_id, name) VALUES (?, ?)",
        [("wild nothing", "Wild Nothing"), ("beach fossils", "Beach Fossils")],
    )
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, spotify_id, status)"
        " VALUES (?, ?, ?, ?, 'candidate')",
        [
            ("wild nothing|||shadow", "Shadow", "wild nothing", "spotify:track:shadow1"),
            ("wild nothing|||chinatown", "Chinatown", "wild nothing", "spotify:track:china1"),
            ("wild nothing|||golden haze", "Golden Haze", "wild nothing", "spotify:track:goldw"),
            ("beach fossils|||golden haze", "Golden Haze", "beach fossils", "spotify:track:goldb"),
            ("beach fossils|||daydream", "Daydream", "beach fossils", None),  # pre-capture
        ],
    )
    conn.executemany(
        "INSERT INTO spotify_playlists (spotify_playlist_id, name) VALUES (?, ?)",
        [("pl_mix", "My Mix"), ("pl_chill", "Chill")],
    )
    conn.executemany(
        "INSERT INTO playlist_tracks (spotify_playlist_id, track_id, position) VALUES (?, ?, ?)",
        [
            ("pl_mix", "wild nothing|||shadow", 0),
            ("pl_mix", "beach fossils|||daydream", 1),
            ("pl_mix", "wild nothing|||golden haze", 2),
            ("pl_chill", "wild nothing|||chinatown", 0),
            ("pl_chill", "beach fossils|||golden haze", 1),
        ],
    )
    conn.commit()
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    cli._spotify = MagicMock()
    cli._db = MagicMock()
    cli._undo_stack = []
    cli._spotify.get_playlist_tracks.side_effect = lambda name: [
        dict(track) for track in SNAPSHOTS.get(name, [])
    ]
    cli._spotify.append_to_playlist.return_value = True
    cli._spotify.remove_from_playlist.return_value = True
    cli._spotify.replace_playlist_items.return_value = True
    cli._spotify.search_song.return_value = None
    return cli


def _mirror_ids(cli, playlist_id):
    rows = cli.repos.playlist_tracks.list_for_playlist(playlist_id)
    return [row["track_id"] for row in rows]


# ---- resolution -----------------------------------------------------------


class TestResolution:
    def test_exact_artist_dash_name_hit(self, tmp_path):
        cli = _cli(tmp_path)
        assert cli.add_track_to_playlist("Wild Nothing - Shadow", "Chill") is True
        name, songs = cli._spotify.append_to_playlist.call_args[0]
        assert name == "Chill"
        assert [s.id for s in songs] == ["wild nothing|||shadow"]
        assert songs[0].spotify_uri == "spotify:track:shadow1"

    def test_fuzzy_hit_without_dash(self, tmp_path):
        cli = _cli(tmp_path)
        assert cli.add_track_to_playlist("wild nothing shadow", "Chill") is True
        _, songs = cli._spotify.append_to_playlist.call_args[0]
        assert [s.id for s in songs] == ["wild nothing|||shadow"]

    def test_id_bypass_skips_fuzzy(self, tmp_path):
        cli = _cli(tmp_path)
        ok = cli.add_track_to_playlist("", "Chill", track_id="beach fossils|||daydream")
        assert ok is True
        _, songs = cli._spotify.append_to_playlist.call_args[0]
        assert [s.id for s in songs] == ["beach fossils|||daydream"]

    def test_miss_prints_near_misses_and_fails(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        assert cli.add_track_to_playlist("zzzz qqqq xxxx", "Chill") is False
        out = capsys.readouterr().out
        assert "No confident match" in out
        assert "--id" in out
        cli._spotify.append_to_playlist.assert_not_called()
        assert cli._undo_stack == []

    def test_ambiguity_fails_loudly_with_top_matches(self, tmp_path, capsys):
        """Two mirrored 'Golden Haze' tracks: no wizard, just the near-misses."""
        cli = _cli(tmp_path)
        assert cli.add_track_to_playlist("golden haze", "Chill") is False
        out = capsys.readouterr().out
        assert "ambiguous" in out
        assert "wild nothing|||golden haze" in out
        assert "beach fossils|||golden haze" in out
        cli._spotify.append_to_playlist.assert_not_called()

    def test_unknown_id_fails(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        assert cli.add_track_to_playlist("", "Chill", track_id="nope|||nothing") is False
        assert "No track with id" in capsys.readouterr().out

    def test_empty_query_without_id_fails(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        assert cli.add_track_to_playlist("", "Chill") is False
        assert "query or --id" in capsys.readouterr().out

    def test_remove_restricts_candidates_to_source_playlist(self, tmp_path):
        """'golden haze' is ambiguous library-wide but unique within My Mix."""
        cli = _cli(tmp_path)
        assert cli.remove_track_from_playlist("golden haze", "My Mix") is True
        name, uris = cli._spotify.remove_from_playlist.call_args[0]
        assert name == "My Mix"
        assert uris == ["spotify:track:goldw"]

    def test_confirmation_line_shows_lives_in(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        cli.add_track_to_playlist("Wild Nothing - Shadow", "Chill")
        assert "lives in: My Mix" in capsys.readouterr().out


# ---- /add -----------------------------------------------------------------


class TestAddTrack:
    def test_add_records_undo_and_patches_mirror(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        assert cli.add_track_to_playlist("Wild Nothing - Shadow", "Chill") is True
        # Undo snapshot captured the pre-write contents.
        assert cli._undo_stack == [{"playlist": "Chill", "tracks": SNAPSHOTS["Chill"]}]
        # Mirror row appended at the next free position.
        rows = cli.repos.playlist_tracks.list_for_playlist("pl_chill")
        assert rows[-1]["track_id"] == "wild nothing|||shadow"
        assert rows[-1]["position"] == 2
        assert "Run /undo to revert" in capsys.readouterr().out

    def test_add_undo_round_trip(self, tmp_path):
        cli = _cli(tmp_path)
        cli.add_track_to_playlist("Wild Nothing - Shadow", "Chill")
        assert cli.undo_last_write() is True
        name, songs = cli._spotify.replace_playlist_items.call_args[0]
        assert name == "Chill"
        assert [s.spotify_uri for s in songs] == ["spotify:track:china1"]
        assert cli._undo_stack == []

    def test_add_failure_records_no_undo_and_leaves_mirror(self, tmp_path):
        cli = _cli(tmp_path)
        cli._spotify.append_to_playlist.return_value = False
        assert cli.add_track_to_playlist("Wild Nothing - Shadow", "Chill") is False
        assert cli._undo_stack == []
        assert "wild nothing|||shadow" not in _mirror_ids(cli, "pl_chill")

    def test_add_to_unmirrored_playlist_skips_mirror_patch(self, tmp_path):
        cli = _cli(tmp_path)
        assert cli.add_track_to_playlist("Wild Nothing - Shadow", "Brand New") is True
        assert len(cli._undo_stack) == 1  # undo still works; mirror untouched
        assert cli.repos.playlist_tracks.count() == 5

    def test_add_duplicate_membership_does_not_break_mirror(self, tmp_path):
        cli = _cli(tmp_path)
        assert cli.add_track_to_playlist("Wild Nothing - Shadow", "My Mix") is True
        assert _mirror_ids(cli, "pl_mix").count("wild nothing|||shadow") == 1


# ---- /remove --------------------------------------------------------------


class TestRemoveTrack:
    def test_remove_uses_stored_uri_and_patches_mirror(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        assert cli.remove_track_from_playlist("Wild Nothing - Shadow", "My Mix") is True
        name, uris = cli._spotify.remove_from_playlist.call_args[0]
        assert (name, uris) == ("My Mix", ["spotify:track:shadow1"])
        assert "wild nothing|||shadow" not in _mirror_ids(cli, "pl_mix")
        out = capsys.readouterr().out
        assert "all occurrences" in out  # documents the all-duplicates removal
        assert "Run /undo to revert" in out

    def test_remove_undo_round_trip_restores_snapshot(self, tmp_path):
        cli = _cli(tmp_path)
        cli.remove_track_from_playlist("Wild Nothing - Shadow", "My Mix")
        assert cli.undo_last_write() is True
        name, songs = cli._spotify.replace_playlist_items.call_args[0]
        assert name == "My Mix"
        # The snapshot restores the duplicates a remove-all wiped out.
        assert [s.spotify_uri for s in songs] == ["spotify:track:shadow1", "spotify:track:day1"]

    def test_remove_pre_capture_row_uses_live_search_fallback(self, tmp_path):
        cli = _cli(tmp_path)
        cli._spotify.search_song.return_value = "spotify:track:live1"
        assert cli.remove_track_from_playlist("Beach Fossils - Daydream", "My Mix") is True
        cli._spotify.search_song.assert_called_once()
        _, uris = cli._spotify.remove_from_playlist.call_args[0]
        assert uris == ["spotify:track:live1"]

    def test_remove_without_any_uri_fails_before_writing(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        cli._spotify.search_song.return_value = None
        assert cli.remove_track_from_playlist("Beach Fossils - Daydream", "My Mix") is False
        cli._spotify.remove_from_playlist.assert_not_called()
        assert cli._undo_stack == []
        assert "No Spotify URI" in capsys.readouterr().out

    def test_remove_failure_keeps_mirror_row(self, tmp_path):
        cli = _cli(tmp_path)
        cli._spotify.remove_from_playlist.return_value = False
        assert cli.remove_track_from_playlist("Wild Nothing - Shadow", "My Mix") is False
        assert cli._undo_stack == []
        assert "wild nothing|||shadow" in _mirror_ids(cli, "pl_mix")


# ---- /move ----------------------------------------------------------------


class TestMoveTrack:
    def test_move_snapshots_both_playlists_before_either_write(self, tmp_path):
        cli = _cli(tmp_path)
        assert cli.move_track("Wild Nothing - Shadow", "My Mix", "Chill") is True
        ops = [name for name, args, kwargs in cli._spotify.mock_calls]
        first_write = min(ops.index("remove_from_playlist"), ops.index("append_to_playlist"))
        snapshot_indexes = [i for i, op in enumerate(ops) if op == "get_playlist_tracks"]
        assert len(snapshot_indexes) == 2
        assert all(i < first_write for i in snapshot_indexes)

    def test_move_patches_both_mirror_sides(self, tmp_path):
        cli = _cli(tmp_path)
        cli.move_track("Wild Nothing - Shadow", "My Mix", "Chill")
        assert "wild nothing|||shadow" not in _mirror_ids(cli, "pl_mix")
        assert "wild nothing|||shadow" in _mirror_ids(cli, "pl_chill")

    def test_move_undo_round_trip_is_lifo_dest_then_source(self, tmp_path):
        cli = _cli(tmp_path)
        cli.move_track("Wild Nothing - Shadow", "My Mix", "Chill")
        assert len(cli._undo_stack) == 2
        # First /undo reverts the destination append...
        assert cli.undo_last_write() is True
        name, songs = cli._spotify.replace_playlist_items.call_args[0]
        assert name == "Chill"
        assert [s.spotify_uri for s in songs] == ["spotify:track:china1"]
        # ...the second reverts the source removal.
        assert cli.undo_last_write() is True
        name, songs = cli._spotify.replace_playlist_items.call_args[0]
        assert name == "My Mix"
        assert [s.spotify_uri for s in songs] == ["spotify:track:shadow1", "spotify:track:day1"]
        assert cli._undo_stack == []

    def test_move_remove_failure_leaves_everything_untouched(self, tmp_path):
        cli = _cli(tmp_path)
        cli._spotify.remove_from_playlist.return_value = False
        assert cli.move_track("Wild Nothing - Shadow", "My Mix", "Chill") is False
        cli._spotify.append_to_playlist.assert_not_called()
        assert cli._undo_stack == []
        assert "wild nothing|||shadow" in _mirror_ids(cli, "pl_mix")

    def test_move_append_failure_keeps_source_undo(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        cli._spotify.append_to_playlist.return_value = False
        assert cli.move_track("Wild Nothing - Shadow", "My Mix", "Chill") is False
        # The source removal DID happen: its undo entry (and mirror patch) stay.
        assert [entry["playlist"] for entry in cli._undo_stack] == ["My Mix"]
        assert "wild nothing|||shadow" not in _mirror_ids(cli, "pl_mix")
        assert "wild nothing|||shadow" not in _mirror_ids(cli, "pl_chill")
        out = capsys.readouterr().out
        assert "failed to add" in out
        assert "/undo" in out

    def test_move_resolves_within_source_membership(self, tmp_path):
        """'golden haze' is ambiguous library-wide but unique within Chill."""
        cli = _cli(tmp_path)
        assert cli.move_track("golden haze", "Chill", "My Mix") is True
        _, uris = cli._spotify.remove_from_playlist.call_args[0]
        assert uris == ["spotify:track:goldb"]


# ---- TUI destructive-command gating ---------------------------------------


class TestDestructiveQuestions:
    """/remove and /move get a confirm question; /add stays ungated.

    _destructive_question reads only (command, args), so it is called unbound
    here — no Textual app construction needed.
    """

    def _question(self, command, args):
        from interactive_app import PlaylistInteractiveApp

        return PlaylistInteractiveApp._destructive_question(None, command, args)

    def test_remove_and_move_are_gated(self):
        from types import SimpleNamespace

        remove_q = self._question(
            "remove", SimpleNamespace(query=["shadow"], from_playlist="My Mix")
        )
        assert "My Mix" in remove_q
        assert "occurrences" in remove_q
        move_q = self._question(
            "move", SimpleNamespace(query=["shadow"], from_playlist="My Mix", to_playlist="Chill")
        )
        assert "My Mix" in move_q and "Chill" in move_q

    def test_add_is_not_gated(self):
        from types import SimpleNamespace

        assert self._question("add", SimpleNamespace(query=["shadow"], to_playlist="Chill")) is None
