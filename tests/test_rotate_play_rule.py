"""Rotation must follow the canonical play rule (plays.py).

A listen_events row counts as a play iff ``ms_played IS NULL OR ms_played >=
30000``. Before this regression test, /rotate treated EVERY event after a
track's added_at as a play — so a 5-second skip recorded by a GDPR import
would rotate the track out even though, by the project's own rule (and
Spotify's ledger rule), it was never played.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

import pytest

import ui
from main import PlaylistCLI
from storage.migrations import ensure_schema
from storage.repos import Repositories


class FakeSpotify:
    """Just enough SpotifyManager surface for rotate_playlist_played."""

    def __init__(self, tracks: List[Dict[str, Any]]) -> None:
        self.tracks = tracks
        self.replace_calls: List[List[Any]] = []

    def get_playlist_tracks(self, name: str) -> List[Dict[str, Any]]:
        return list(self.tracks)

    def replace_playlist_items(self, name: str, songs: List[Any]) -> bool:
        self.replace_calls.append(songs)
        return True


@pytest.fixture(autouse=True)
def _no_sink():
    ui.set_output_sink(None)
    yield
    ui.set_output_sink(None)


def _make_cli(playlist_tracks: List[Dict[str, Any]]) -> PlaylistCLI:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    cli._spotify = FakeSpotify(playlist_tracks)
    return cli


def _seed_track(cli: PlaylistCLI, artist: str, name: str) -> str:
    track_id = f"{artist.lower()}|||{name.lower()}"
    cli.repos.artists.upsert(artist_id=artist.lower(), name=artist)
    cli.repos.tracks.upsert(
        {
            "track_id": track_id,
            "name": name,
            "artist_id": artist.lower(),
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
        }
    )
    return track_id


def _seed_event(cli: PlaylistCLI, track_id: str, played_at: str, ms_played=None, skipped=None):
    cli.repos.listen_events.upsert(
        {
            "event_id": f"event-{track_id}-{played_at}",
            "track_id": track_id,
            "spotify_id": "abc123",
            "played_at": played_at,
            "source": "gdpr_export" if ms_played is not None else "recently_played",
            "created_at": played_at,
            "ms_played": ms_played,
            "skipped": skipped,
        }
    )
    cli.repos.conn.commit()


_PLAYLIST = [
    {
        "name": "Song One",
        "artist": "Artist A",
        "uri": "spotify:track:abc123",
        "added_at": "2026-06-05T00:00:00Z",
    }
]


def test_sub_30s_gdpr_skip_does_not_rotate_track_out(capsys):
    """A 5s skip after added_at is NOT a play — the track stays."""
    cli = _make_cli(_PLAYLIST)
    track_id = _seed_track(cli, "Artist A", "Song One")
    _seed_event(cli, track_id, "2026-06-06T00:00:00Z", ms_played=5000, skipped=1)

    cli.rotate_playlist_played("My Mix")

    assert cli.spotify.replace_calls == []  # nothing rotated
    assert "No played tracks detected" in capsys.readouterr().out


def test_full_play_still_rotates_track_out():
    """A >=30s play (or a polled event with unknown duration) still rotates."""
    cli = _make_cli(_PLAYLIST)
    track_id = _seed_track(cli, "Artist A", "Song One")
    _seed_event(cli, track_id, "2026-06-06T00:00:00Z", ms_played=215000, skipped=0)

    cli.rotate_playlist_played("My Mix")

    assert len(cli.spotify.replace_calls) == 1
    kept_ids = [song.id for song in cli.spotify.replace_calls[0]]
    assert track_id not in kept_ids  # played track was removed


def test_polled_event_with_unknown_duration_counts_as_play():
    """ms_played is NULL for recently_played polling — still a play."""
    cli = _make_cli(_PLAYLIST)
    track_id = _seed_track(cli, "Artist A", "Song One")
    _seed_event(cli, track_id, "2026-06-06T00:00:00Z")

    cli.rotate_playlist_played("My Mix")

    assert len(cli.spotify.replace_calls) == 1
    kept_ids = [song.id for song in cli.spotify.replace_calls[0]]
    assert track_id not in kept_ids
