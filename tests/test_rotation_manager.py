"""Listen-aware selection in RotationManager.

``_select_songs_with_history`` consults the real listen ledger when repos are
available: ``plays.last_played_map`` overrides the one-generation-per-day
freshness estimate whenever the real timestamp is more recent (max of the
two), and ``plays.recency_weights`` demotes candidates within their tier.
Tracks absent from the ledger — and managers without repos — behave exactly
as before. All offline; in-memory SQLite via ensure_schema.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

import ui
from models import Song
from rotation_manager import RotationManager
from storage.migrations import ensure_schema
from storage.repos import Repositories


class FakeDB:
    """Just enough SongStore surface for selection."""

    def __init__(self, songs: List[Song]) -> None:
        self._songs = songs

    def get_all_songs(self) -> List[Song]:
        return list(self._songs)

    def find_similar_songs(self, song, k=1, threshold=0.9):
        # Not used in these test paths (unused/fresh songs fill the count).
        return []


@pytest.fixture(autouse=True)
def _no_sink():
    ui.set_output_sink(None)
    yield
    ui.set_output_sink(None)


def _repos() -> Repositories:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return Repositories(conn)


def _song(artist: str, name: str) -> Song:
    return Song(id=f"{artist.lower()}|||{name.lower()}", name=name, artist=artist)


def _seed_track(repos: Repositories, song: Song) -> None:
    repos.artists.upsert(artist_id=song.artist.lower(), name=song.artist)
    repos.tracks.upsert(
        {
            "track_id": song.id,
            "name": song.name,
            "artist_id": song.artist.lower(),
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
        }
    )


def _seed_play(
    repos: Repositories, track_id: str, played_at: str, ms_played: Optional[int] = None
) -> None:
    repos.listen_events.upsert(
        {
            "event_id": f"event-{track_id}-{played_at}",
            "track_id": track_id,
            "spotify_id": "abc123",
            "played_at": played_at,
            "source": "recently_played",
            "created_at": played_at,
            "ms_played": ms_played,
            "skipped": None,
        }
    )
    repos.conn.commit()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _manager(songs: List[Song], repos: Optional[Repositories] = None) -> RotationManager:
    return RotationManager("Test Playlist", db=FakeDB(songs), spotify=object(), repos=repos)


class TestListenAwareSelection:
    def test_no_listen_data_selects_exactly_as_before(self):
        """Empty ledger == pre-listen-aware behavior (strictly additive)."""
        songs = [_song("a", "one"), _song("b", "two"), _song("c", "three")]
        repos = _repos()
        for song in songs:
            _seed_track(repos, song)

        with_repos = _manager(songs, repos)
        without_repos = _manager(songs, None)

        assert [s.id for s in with_repos.select_songs_for_today(count=2)] == [
            s.id for s in without_repos.select_songs_for_today(count=2)
        ]

    def test_real_last_played_overrides_stale_generation_estimate(self):
        a, b = _song("a", "one"), _song("b", "two")
        repos = _repos()
        _seed_track(repos, a)
        _seed_track(repos, b)
        rm = _manager([a, b], repos)
        # Both tracks sit in generation 0 of a 40-generation history, so the
        # one-generation-per-day estimate says "used ~40 days ago" — fresh.
        rm.history.generations = [[a.id, b.id]] + [["x|||filler"]] * 39

        # By estimate alone both are fresh and db order wins: a first.
        assert [s.id for s in rm.select_songs_for_today(count=1, fresh_days=30)] == [a.id]

        # A real play of `a` yesterday overrides the stale estimate — it is
        # no longer fresh, so the still-fresh `b` is selected instead.
        _seed_play(repos, a.id, _iso(datetime.now(timezone.utc) - timedelta(days=1)))
        assert [s.id for s in rm.select_songs_for_today(count=1, fresh_days=30)] == [b.id]

    def test_recent_listen_mass_demotes_within_tier(self):
        a, b = _song("a", "one"), _song("b", "two")
        repos = _repos()
        _seed_track(repos, a)
        _seed_track(repos, b)
        rm = _manager([a, b], repos)  # empty history: both never-used (tier 1)

        # Without listen data the db order wins: a first.
        assert [s.id for s in rm.select_songs_for_today(count=1)] == [a.id]

        # A recent real play of `a` demotes it below the unheard `b`.
        _seed_play(repos, a.id, _iso(datetime.now(timezone.utc) - timedelta(hours=2)))
        assert [s.id for s in rm.select_songs_for_today(count=1)] == [b.id]

    def test_tracks_absent_from_ledger_keep_relative_order(self):
        songs = [_song("a", "one"), _song("b", "two"), _song("c", "three")]
        repos = _repos()
        for song in songs:
            _seed_track(repos, song)
        rm = _manager(songs, repos)
        _seed_play(repos, songs[0].id, _iso(datetime.now(timezone.utc) - timedelta(hours=1)))

        selected = rm.select_songs_for_today(count=3)
        # The played track is demoted to last; the unheard two keep db order.
        assert [s.id for s in selected] == [songs[1].id, songs[2].id, songs[0].id]

    def test_sub30s_skip_is_not_listen_data(self):
        """The canonical 30s play rule holds: a skip never demotes."""
        a, b = _song("a", "one"), _song("b", "two")
        repos = _repos()
        _seed_track(repos, a)
        _seed_track(repos, b)
        rm = _manager([a, b], repos)
        _seed_play(
            repos, a.id, _iso(datetime.now(timezone.utc) - timedelta(hours=1)), ms_played=5000
        )

        assert [s.id for s in rm.select_songs_for_today(count=1)] == [a.id]


class TestHonestyLine:
    def test_reports_listen_data_coverage(self, capsys):
        songs = [_song("a", "one"), _song("b", "two"), _song("c", "three")]
        repos = _repos()
        for song in songs:
            _seed_track(repos, song)
        rm = _manager(songs, repos)
        _seed_play(repos, songs[0].id, _iso(datetime.now(timezone.utc) - timedelta(days=2)))

        rm.select_songs_for_today(count=2)
        assert "1 of 3 candidates had real listen data." in capsys.readouterr().out

    def test_reports_zero_without_repos(self, capsys):
        songs = [_song("a", "one"), _song("b", "two")]
        rm = _manager(songs, None)

        rm.select_songs_for_today(count=1)
        assert "0 of 2 candidates had real listen data." in capsys.readouterr().out
