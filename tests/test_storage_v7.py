from __future__ import annotations

import sqlite3
from pathlib import Path

from storage.db import Database
from storage.migrations import LATEST_VERSION, ensure_schema
from storage.repos import Repositories


def _connect(tmp_path: Path) -> sqlite3.Connection:
    db = Database(tmp_path / "test.db")
    conn = db.connect()
    ensure_schema(conn)
    return conn


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1;").fetchone()
    return int(row[0] if isinstance(row, tuple) else row["version"])


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    return {row["name"] for row in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()
    return {row["name"] for row in rows}


def _seed_track(repos: Repositories, artist: str, name: str) -> str:
    """Upsert artist + track rows (FK order matters) and return the track_id."""
    track_id = f"{artist.lower()}|||{name.lower()}"
    repos.artists.upsert(artist_id=artist.lower(), name=artist)
    repos.tracks.upsert(
        {
            "track_id": track_id,
            "name": name,
            "artist_id": artist.lower(),
            "created_at": "2026-06-10T00:00:00Z",
            "updated_at": "2026-06-10T00:00:00Z",
        }
    )
    return track_id


# ---------------------------------------------------------------------------
# Migration


def test_fresh_schema_reaches_v7(tmp_path: Path) -> None:
    conn = _connect(tmp_path)

    assert LATEST_VERSION == 7
    assert _schema_version(conn) == 7

    names = _table_names(conn)
    assert "sync_state" in names
    assert "spotify_playlists" in names
    assert "playlist_tracks" in names
    assert "liked_tracks" in names

    listen_cols = {r[1] for r in conn.execute("PRAGMA table_info(listen_events);")}
    assert {"ms_played", "skipped", "context_uri"} <= listen_cols

    indexes = _index_names(conn)
    assert "idx_playlist_tracks_track" in indexes
    assert "idx_liked_tracks_added" in indexes


def test_ensure_schema_idempotent(tmp_path: Path) -> None:
    conn = _connect(tmp_path)
    ensure_schema(conn)
    assert _schema_version(conn) == 7


# ---------------------------------------------------------------------------
# SyncStateRepo


def test_sync_state_roundtrip(tmp_path: Path) -> None:
    repos = Repositories(_connect(tmp_path))

    assert repos.sync_state.get("recently_played") is None

    repos.sync_state.set("recently_played", "cursor-1", "2026-06-10T00:00:00Z")
    state = repos.sync_state.get("recently_played")
    assert state is not None
    assert state["cursor"] == "cursor-1"
    assert state["last_synced_at"] == "2026-06-10T00:00:00Z"

    # Upsert overwrites the cursor for the same source.
    repos.sync_state.set("recently_played", "cursor-2", "2026-06-10T01:00:00Z")
    state = repos.sync_state.get("recently_played")
    assert state is not None
    assert state["cursor"] == "cursor-2"
    assert state["last_synced_at"] == "2026-06-10T01:00:00Z"

    # Sources are independent rows.
    repos.sync_state.set("liked", None, "2026-06-10T02:00:00Z")
    liked = repos.sync_state.get("liked")
    assert liked is not None
    assert liked["cursor"] is None
    recent = repos.sync_state.get("recently_played")
    assert recent is not None
    assert recent["cursor"] == "cursor-2"


# ---------------------------------------------------------------------------
# SpotifyPlaylistsRepo


def test_spotify_playlists_roundtrip_and_delete_missing(tmp_path: Path) -> None:
    repos = Repositories(_connect(tmp_path))

    repos.spotify_playlists.upsert(
        "pl-1",
        "Beach Mix",
        owner="tanuj",
        is_owned=1,
        snapshot_id="snap-1",
        total_tracks=10,
        synced_at="2026-06-10T00:00:00Z",
    )
    repos.spotify_playlists.upsert("pl-2", "Algo Mix", owner="spotify")

    got = repos.spotify_playlists.get("pl-1")
    assert got is not None
    assert got["name"] == "Beach Mix"
    assert got["is_owned"] == 1
    assert got["snapshot_id"] == "snap-1"
    assert got["total_tracks"] == 10

    # list_all is name-ordered.
    assert [p["spotify_playlist_id"] for p in repos.spotify_playlists.list_all()] == [
        "pl-2",
        "pl-1",
    ]

    # Upsert updates in place.
    repos.spotify_playlists.upsert("pl-1", "Beach Mix v2", snapshot_id="snap-2")
    got = repos.spotify_playlists.get("pl-1")
    assert got is not None
    assert got["name"] == "Beach Mix v2"
    assert got["snapshot_id"] == "snap-2"

    deleted = repos.spotify_playlists.delete_missing(["pl-1"])
    assert deleted == 1
    assert repos.spotify_playlists.get("pl-2") is None
    assert [p["spotify_playlist_id"] for p in repos.spotify_playlists.list_all()] == ["pl-1"]

    # Empty keep list wipes everything.
    deleted = repos.spotify_playlists.delete_missing([])
    assert deleted == 1
    assert repos.spotify_playlists.list_all() == []


def test_delete_missing_cascades_playlist_tracks(tmp_path: Path) -> None:
    repos = Repositories(_connect(tmp_path))
    track_id = _seed_track(repos, "Artist A", "Song One")

    repos.spotify_playlists.upsert("pl-1", "Keep Me")
    repos.spotify_playlists.upsert("pl-2", "Drop Me")
    repos.playlist_tracks.replace_for_playlist("pl-1", [{"track_id": track_id, "position": 0}])
    repos.playlist_tracks.replace_for_playlist("pl-2", [{"track_id": track_id, "position": 0}])
    assert repos.playlist_tracks.count() == 2

    assert repos.spotify_playlists.delete_missing(["pl-1"]) == 1
    # ON DELETE CASCADE removed pl-2's membership rows.
    assert repos.playlist_tracks.count() == 1
    assert repos.playlist_tracks.list_for_playlist("pl-2") == []


# ---------------------------------------------------------------------------
# PlaylistTracksRepo


def test_playlist_tracks_replace_and_queries(tmp_path: Path) -> None:
    conn = _connect(tmp_path)
    repos = Repositories(conn)
    track_a = _seed_track(repos, "Artist A", "Song One")
    track_b = _seed_track(repos, "Artist B", "Song Two")

    repos.spotify_playlists.upsert("pl-1", "Mix One")
    repos.spotify_playlists.upsert("pl-2", "Mix Two")

    repos.playlist_tracks.replace_for_playlist(
        "pl-1",
        [
            {
                "track_id": track_b,
                "added_at": "2026-06-01T00:00:00Z",
                "position": 1,
                "synced_at": "2026-06-10T00:00:00Z",
            },
            {
                "track_id": track_a,
                "added_at": "2026-06-02T00:00:00Z",
                "position": 0,
                "synced_at": "2026-06-10T00:00:00Z",
            },
        ],
    )
    repos.playlist_tracks.replace_for_playlist("pl-2", [{"track_id": track_a, "position": 0}])
    conn.commit()

    # list_for_playlist is position-ordered.
    rows = repos.playlist_tracks.list_for_playlist("pl-1")
    assert [r["track_id"] for r in rows] == [track_a, track_b]
    assert rows[0]["added_at"] == "2026-06-02T00:00:00Z"

    # playlists_for_track joins back to playlist rows (name-ordered).
    names = [p["name"] for p in repos.playlist_tracks.playlists_for_track(track_a)]
    assert names == ["Mix One", "Mix Two"]
    assert [p["name"] for p in repos.playlist_tracks.playlists_for_track(track_b)] == ["Mix One"]

    assert repos.playlist_tracks.count() == 3

    # Replace fully swaps membership (delete-then-insert).
    repos.playlist_tracks.replace_for_playlist("pl-1", [{"track_id": track_b, "position": 0}])
    conn.commit()
    assert [r["track_id"] for r in repos.playlist_tracks.list_for_playlist("pl-1")] == [track_b]
    assert repos.playlist_tracks.count() == 2

    # Replace with no rows empties the playlist.
    repos.playlist_tracks.replace_for_playlist("pl-1", [])
    conn.commit()
    assert repos.playlist_tracks.list_for_playlist("pl-1") == []
    assert repos.playlist_tracks.count() == 1


# ---------------------------------------------------------------------------
# LikedTracksRepo


def test_liked_tracks_roundtrip_and_prune(tmp_path: Path) -> None:
    repos = Repositories(_connect(tmp_path))
    track_a = _seed_track(repos, "Artist A", "Song One")
    track_b = _seed_track(repos, "Artist B", "Song Two")
    track_c = _seed_track(repos, "Artist C", "Song Three")

    repos.liked_tracks.upsert(track_a, "2026-06-01T00:00:00Z", "2026-06-10T00:00:00Z")
    repos.liked_tracks.upsert(track_b, "2026-06-03T00:00:00Z", "2026-06-10T00:00:00Z")
    repos.liked_tracks.upsert(track_c, "2026-06-02T00:00:00Z", "2026-06-10T00:00:00Z")
    assert repos.liked_tracks.count() == 3

    # list_all is newest-liked first.
    assert [r["track_id"] for r in repos.liked_tracks.list_all()] == [track_b, track_c, track_a]

    # Upsert updates an existing like.
    repos.liked_tracks.upsert(track_a, "2026-06-04T00:00:00Z", "2026-06-11T00:00:00Z")
    rows = repos.liked_tracks.list_all()
    assert rows[0]["track_id"] == track_a
    assert rows[0]["synced_at"] == "2026-06-11T00:00:00Z"

    pruned = repos.liked_tracks.prune_missing([track_a, track_c])
    assert pruned == 1
    assert {r["track_id"] for r in repos.liked_tracks.list_all()} == {track_a, track_c}

    # Empty keep list wipes everything.
    assert repos.liked_tracks.prune_missing([]) == 2
    assert repos.liked_tracks.count() == 0
    assert repos.liked_tracks.list_all() == []


def test_prune_missing_handles_more_than_999_keep_ids(tmp_path: Path) -> None:
    """Regression: a >999-track liked library must not blow SQLite's
    bound-variable limit (999 on SQLite < 3.32 — the py3.9 floor era)."""
    conn = _connect(tmp_path)
    repos = Repositories(conn)
    track_ids = []
    for i in range(1205):
        track_id = _seed_track(repos, f"Artist {i}", f"Song {i}")
        repos.liked_tracks.upsert(track_id, "2026-06-01T00:00:00Z", "2026-06-10T00:00:00Z")
        track_ids.append(track_id)

    keep = track_ids[:1200]  # 5 unliked, >999 kept
    pruned = repos.liked_tracks.prune_missing(keep)
    assert pruned == 5
    assert repos.liked_tracks.count() == 1200

    # Duplicates in keep_ids must not break the delete either.
    assert repos.liked_tracks.prune_missing(keep + keep) == 0
    assert repos.liked_tracks.count() == 1200


def test_delete_missing_handles_more_than_999_keep_ids(tmp_path: Path) -> None:
    repos = Repositories(_connect(tmp_path))
    for i in range(1100):
        repos.spotify_playlists.upsert(f"pl-{i}", f"Mix {i}")

    keep = [f"pl-{i}" for i in range(1000)]
    assert repos.spotify_playlists.delete_missing(keep) == 100
    assert len(repos.spotify_playlists.list_all()) == 1000


# ---------------------------------------------------------------------------
# listen_events COALESCE semantics


def test_listen_events_upsert_enriches_without_clobbering(tmp_path: Path) -> None:
    repos = Repositories(_connect(tmp_path))
    track_id = _seed_track(repos, "Artist A", "Song One")

    # 1) First seen via polling: no telemetry columns.
    repos.listen_events.upsert(
        {
            "event_id": "event-1",
            "track_id": track_id,
            "spotify_id": "spotify:track:abc",
            "played_at": "2026-06-10T01:00:00Z",
            "source": "recently_played",
            "created_at": "2026-06-10T01:00:01Z",
        }
    )
    event = repos.listen_events.list_by_track(track_id)[0]
    assert event["ms_played"] is None
    assert event["skipped"] is None
    assert event["context_uri"] is None

    # 2) GDPR re-import enriches the same event with telemetry.
    repos.listen_events.upsert(
        {
            "event_id": "event-1",
            "track_id": track_id,
            "spotify_id": "spotify:track:abc",
            "played_at": "2026-06-10T01:00:00Z",
            "source": "gdpr_export",
            "created_at": "2026-06-10T01:00:01Z",
            "ms_played": 1234,
            "skipped": 0,
            "context_uri": "spotify:playlist:xyz",
        }
    )
    event = repos.listen_events.list_by_track(track_id)[0]
    assert event["ms_played"] == 1234
    assert event["skipped"] == 0
    assert event["context_uri"] == "spotify:playlist:xyz"
    assert event["source"] == "gdpr_export"

    # 3) A later None-payload upsert must NOT erase the telemetry.
    repos.listen_events.upsert(
        {
            "event_id": "event-1",
            "track_id": track_id,
            "spotify_id": "spotify:track:abc",
            "played_at": "2026-06-10T01:00:00Z",
            "source": "recently_played",
            "created_at": "2026-06-10T01:00:01Z",
        }
    )
    event = repos.listen_events.list_by_track(track_id)[0]
    assert event["ms_played"] == 1234
    assert event["skipped"] == 0
    assert event["context_uri"] == "spotify:playlist:xyz"
