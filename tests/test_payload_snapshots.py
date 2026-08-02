"""Golden payload-structure snapshots for /profile, /stats, and /taste.

These pin the exact key structure of the ``--json`` payload dicts that
``show_profile`` / ``show_stats`` / ``show_taste`` return, so the planned
compute/render splits can prove the verbatim guarantee: if a refactor adds,
drops, or renames a key anywhere in the payload tree, the diff shows up here.
Values are deliberately NOT snapshotted (they carry timestamps and sizes);
existing tests already assert the load-bearing ones.

To regenerate after an INTENTIONAL payload change:
    UPDATE_SNAPSHOTS=1 .venv/bin/python -m pytest tests/test_payload_snapshots.py
and commit the snapshot diff alongside the change that caused it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import ui
from main import PlaylistCLI
from song_store import SongStore
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"

EMB = {
    "wild nothing|||a": [1.0, 0.0, 0.0, 0.0],
    "wild nothing|||b": [0.9, 0.1, 0.0, 0.0],
    "wild nothing|||c": [0.8, 0.2, 0.0, 0.0],
    "outlier artist|||z": [0.0, 0.0, 0.0, 1.0],
}
NAMES = {
    "wild nothing|||a": ("Alpha", "wild nothing", "Wild Nothing"),
    "wild nothing|||b": ("Beta", "wild nothing", "Wild Nothing"),
    "wild nothing|||c": ("Gamma", "wild nothing", "Wild Nothing"),
    "outlier artist|||z": ("Zeta", "outlier artist", "Outlier Artist"),
}


@pytest.fixture(autouse=True)
def _quiet():
    """Swallow rendering; these tests are about the returned payload only."""
    ui.set_output_sink(lambda renderable: None)
    yield
    ui.set_output_sink(None)


def _seeded_cli(tmp_path) -> PlaylistCLI:
    """A small but non-degenerate library: tracks, embeddings, a liked track,
    a mirrored playlist, listen events, and one rotation generation — enough
    that every payload section takes its populated (not empty-fallback) path."""
    conn = Database(tmp_path / "tunr.db").connect()
    ensure_schema(conn)
    artists = {(aid, disp) for _, (_, aid, disp) in NAMES.items()}
    conn.executemany("INSERT INTO artists (artist_id, name) VALUES (?, ?)", list(artists))
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status, spotify_id) "
        "VALUES (?, ?, ?, 'candidate', ?)",
        [(tid, NAMES[tid][0], NAMES[tid][1], f"sp{i}") for i, tid in enumerate(EMB)],
    )
    conn.executemany(
        "INSERT INTO track_embeddings "
        "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (tid, "all-mpnet-base-v2", encode_vector(vec), len(vec), vector_norm(vec))
            for tid, vec in EMB.items()
        ],
    )
    conn.execute(
        "INSERT INTO liked_tracks (track_id, added_at) "
        "VALUES ('wild nothing|||a', '2026-05-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO playlists (playlist_id, name, spotify_playlist_id, current_generation) "
        "VALUES ('pl', 'mix', 'sp-pl', 0)"
    )
    conn.execute(
        "INSERT INTO spotify_playlists (spotify_playlist_id, name, is_owned) "
        "VALUES ('sp-pl', 'mix', 1)"
    )
    conn.executemany(
        "INSERT INTO playlist_tracks (spotify_playlist_id, track_id, position) "
        "VALUES ('sp-pl', ?, ?)",
        [(tid, i) for i, tid in enumerate(EMB)],
    )
    conn.execute(
        "INSERT INTO rotation_generations (generation_id, playlist_id, generation_index) "
        "VALUES ('g0','pl',0)"
    )
    conn.executemany(
        "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES ('g0', ?, ?)",
        [(tid, i) for i, tid in enumerate(EMB)],
    )
    conn.executemany(
        "INSERT INTO listen_events (event_id, track_id, played_at, source) "
        "VALUES (?, ?, ?, 'recently_played')",
        [
            (
                f"e{i}",
                tid,
                f"2026-05-2{i}T00:00:00Z",
            )
            for i, tid in enumerate(EMB)
        ],
    )
    conn.commit()

    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    cli._db = SongStore(cli._repos)
    cli._spotify = None
    cli._rotation_managers = {}
    return cli


def _structure(value, path="$"):
    """Flatten a payload into sorted 'path: type' lines. List elements are
    folded to a single [] entry (their union of key paths), so counts don't
    leak into the snapshot — only shape does."""
    lines = set()
    if isinstance(value, dict):
        if not value:
            lines.add(f"{path}: dict")
        for key in value:
            lines |= _structure(value[key], f"{path}.{key}")
    elif isinstance(value, list):
        if not value:
            lines.add(f"{path}[]: empty")
        for item in value:
            lines |= _structure(item, f"{path}[]")
    else:
        lines.add(f"{path}: {type(value).__name__}")
    return lines


def _assert_matches_snapshot(name: str, payload) -> None:
    assert payload is not None, f"{name} returned None — expected a payload dict"
    actual = sorted(_structure(payload))
    snapshot_path = SNAPSHOT_DIR / f"payload_{name}.json"
    if os.getenv("UPDATE_SNAPSHOTS", "0") not in ("", "0"):
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        snapshot_path.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert actual == expected, (
        f"/{name} payload structure changed. If intentional, regenerate with "
        f"UPDATE_SNAPSHOTS=1 and commit the snapshot diff."
    )


def test_profile_payload_structure(tmp_path):
    _assert_matches_snapshot("profile", _seeded_cli(tmp_path).show_profile())


def test_stats_payload_structure(tmp_path):
    _assert_matches_snapshot("stats", _seeded_cli(tmp_path).show_stats())


def test_taste_payload_structure(tmp_path):
    _assert_matches_snapshot("taste", _seeded_cli(tmp_path).show_taste(top=2))
