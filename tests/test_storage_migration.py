from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path
from typing import List, Sequence

from models import PlaylistHistory, Song
from storage.db import Database
from storage.legacy_migrate import migrate_legacy
from storage.migrations import ensure_schema
from storage.repos import Repositories


def _table_names(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    return {row[0] if isinstance(row, tuple) else row["name"] for row in rows}


def _schema_version(conn) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1;").fetchone()
    return int(row[0] if isinstance(row, tuple) else row["version"])


class _FakeEmbedder:
    """Deterministic, dependency-free embedder returning a fixed 4-dim vector."""

    def __init__(self) -> None:
        self.calls: List[Sequence[str]] = []

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        self.calls.append(list(texts))
        out: List[List[float]] = []
        for i, _text in enumerate(texts):
            base = float(i + 1)
            out.append([base, base + 0.1, base + 0.2, base + 0.3])
        return out


def test_schema_v4_tables_and_version(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    conn = db.connect()
    ensure_schema(conn)

    names = _table_names(conn)
    assert "playlists" in names
    assert "rotation_generations" in names
    assert "generation_tracks" in names
    assert "track_sonic" in names  # v5
    assert _schema_version(conn) == 6

    # v6 additive columns: persisted search summary + per-candidate metrics.
    run_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_runs);")}
    cand_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_candidates);")}
    assert "summary" in run_cols
    assert "metrics_json" in cand_cols

    # Idempotent: re-running on a fully-migrated db is a no-op.
    ensure_schema(conn)
    assert _schema_version(conn) == 6


def _write_legacy_fixtures(data_dir: Path) -> None:
    embeddings = data_dir / "embeddings"
    embeddings.mkdir(parents=True, exist_ok=True)
    history = data_dir / "history"
    history.mkdir(parents=True, exist_ok=True)

    songs = {
        "artist a|||song one": Song(
            id="artist a|||song one",
            name="Song One",
            artist="Artist A",
            spotify_uri="spotify:track:aaa",
            first_added=datetime(2024, 1, 1),
        ),
        "artist a|||song two": Song(
            id="artist a|||song two",
            name="Song Two",
            artist="Artist A",
            spotify_uri="spotify:track:bbb",
            first_added=datetime(2024, 1, 2),
        ),
        "artist b|||song three": Song(
            id="artist b|||song three",
            name="Song Three",
            artist="Artist B",
            spotify_uri="spotify:track:ccc",
            first_added=None,
        ),
    }
    with (embeddings / "songs.pkl").open("wb") as fh:
        pickle.dump(songs, fh)

    # One generation references an orphan id not present in songs.pkl.
    hist = PlaylistHistory(
        playlist_id="spotify_playlist_xyz",
        name="My Daily Mix",
        generations=[
            ["artist a|||song one", "artist a|||song two"],
            ["artist b|||song three", "missing|||orphan song"],
        ],
        current_generation=1,
    )
    with (history / "my_daily_mix.pkl").open("wb") as fh:
        pickle.dump(hist, fh)


def _migrate(tmp_path: Path) -> tuple[Repositories, dict]:
    data_dir = tmp_path / "data"
    _write_legacy_fixtures(data_dir)

    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    repos = Repositories(conn)

    report = migrate_legacy(
        repos,
        model_name="test-model",
        data_dir=str(data_dir),
        embedder=_FakeEmbedder(),
        reembed=True,
    )
    return repos, report


def test_migrate_legacy_end_to_end(tmp_path: Path) -> None:
    repos, report = _migrate(tmp_path)

    assert report["songs_loaded"] == 3
    assert report["tracks_upserted"] == 3
    assert report["artists_upserted"] == 2  # Artist A, Artist B
    assert report["embeddings_written"] == 3
    assert report["embeddings_skipped"] == 0
    assert report["embedding_dim"] == 4
    assert report["playlists"] == 1
    assert report["generations"] == 2
    # gen 0: 2 valid tracks; gen 1: 1 valid + 1 orphan -> 3 generation_tracks total.
    assert report["generation_tracks"] == 3
    assert report["orphan_track_ids"] == ["missing|||orphan song"]

    conn = repos.conn
    assert conn.execute("SELECT COUNT(*) FROM tracks;").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM artists;").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM track_embeddings;").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM playlists;").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM rotation_generations;").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM generation_tracks;").fetchone()[0] == 3

    # Every embedding must carry the canonical model name (pipeline-safe).
    models = {
        row[0]
        for row in conn.execute("SELECT DISTINCT model_name FROM track_embeddings;").fetchall()
    }
    assert models == {"test-model"}

    playlist = repos.playlists.get("my_daily_mix")
    assert playlist is not None
    assert playlist["name"] == "My Daily Mix"
    assert playlist["spotify_playlist_id"] == "spotify_playlist_xyz"
    assert playlist["current_generation"] == 1

    gens = list(repos.rotation_generations.list_by_playlist("my_daily_mix"))
    assert [g["generation_index"] for g in gens] == [0, 1]
    first_tracks = list(repos.generation_tracks.list_by_generation(gens[0]["generation_id"]))
    assert [t["track_id"] for t in first_tracks] == ["artist a|||song one", "artist a|||song two"]


def test_migrate_legacy_idempotent(tmp_path: Path) -> None:
    repos, first = _migrate(tmp_path)

    # Re-run against the SAME data dir and connection: counts must be identical
    # except embeddings, which are now skipped instead of written.
    data_dir = tmp_path / "data"
    second = migrate_legacy(
        repos,
        model_name="test-model",
        data_dir=str(data_dir),
        embedder=_FakeEmbedder(),
        reembed=True,
    )

    for key in (
        "songs_loaded",
        "tracks_upserted",
        "artists_upserted",
        "playlists",
        "generations",
        "generation_tracks",
        "orphan_track_ids",
    ):
        assert second[key] == first[key], key

    assert second["embeddings_written"] == 0
    assert second["embeddings_skipped"] == 3

    conn = repos.conn
    assert conn.execute("SELECT COUNT(*) FROM tracks;").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM track_embeddings;").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM rotation_generations;").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM generation_tracks;").fetchone()[0] == 3


def test_migrate_legacy_dry_run_persists_nothing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_legacy_fixtures(data_dir)

    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    repos = Repositories(conn)

    report = migrate_legacy(
        repos,
        model_name="test-model",
        data_dir=str(data_dir),
        embedder=_FakeEmbedder(),
        reembed=True,
        dry_run=True,
    )

    # Report still reflects what would have happened.
    assert report["tracks_upserted"] == 3
    assert report["generation_tracks"] == 3
    # But nothing was committed.
    assert conn.execute("SELECT COUNT(*) FROM tracks;").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM playlists;").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM generation_tracks;").fetchone()[0] == 0
