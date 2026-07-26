"""Tests for src/scopes.py — the single home of data-scope definitions.

Pins the scope counts (JOIN-scoped to live tracks, so orphan side-table rows
never inflate them) and the EXACT caption strings every view reuses, against
a mirror-heavy fixture shaped like the post-/pull reality: a large raw
library mirror with a small enriched curated core inside it. All offline.
"""

from __future__ import annotations

import sqlite3

import pytest

from scopes import (
    LibraryScopes,
    curated_scope_caption,
    enriched_pct_str,
    fmt_n,
    library_scopes,
    mirror_scope_caption,
    plays_scope_caption,
    scopes_payload,
)
from storage.migrations import ensure_schema


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn


def _track(conn, track_id, *, context=False, embedded=False, sonic=False, liked=False):
    artist = track_id.split("|||")[0]
    conn.execute("INSERT OR IGNORE INTO artists (artist_id, name) VALUES (?, ?)", (artist, artist))
    conn.execute(
        "INSERT INTO tracks (track_id, name, artist_id) VALUES (?, ?, ?)",
        (track_id, track_id.split("|||")[1], artist),
    )
    if context:
        conn.execute(
            "INSERT INTO track_context (track_id, fields_json) VALUES (?, '[]')", (track_id,)
        )
    if embedded:
        conn.execute(
            "INSERT INTO track_embeddings (track_id, model_name, embedding_blob, embedding_dim)"
            " VALUES (?, 'stub', ?, 1)",
            (track_id, b"\x00\x00\x00\x00"),
        )
    if sonic:
        conn.execute(
            "INSERT INTO track_sonic (track_id, sonic_blob, sonic_dim) VALUES (?, ?, 1)",
            (track_id, b"\x00\x00\x00\x00"),
        )
    if liked:
        conn.execute("INSERT INTO liked_tracks (track_id) VALUES (?)", (track_id,))


@pytest.fixture
def mirror_heavy():
    """10 raw mirror rows + 3 curated (enriched; 2 embedded, 1 sonic), 4
    liked, 2 rotated — the post-/pull shape in miniature."""
    conn = _connect()
    for i in range(10):  # the raw mirror: no context, no embeddings
        _track(conn, f"mirror|||song {i}", liked=(i < 2))
    _track(conn, "core|||alpha", context=True, embedded=True, sonic=True, liked=True)
    _track(conn, "core|||beta", context=True, embedded=True, liked=True)
    _track(conn, "core|||gamma", context=True)
    conn.execute("INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('p','m',0)")
    conn.execute(
        "INSERT INTO rotation_generations (generation_id, playlist_id, generation_index) "
        "VALUES ('g0','p',0)"
    )
    conn.executemany(
        "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES ('g0', ?, ?)",
        [("core|||alpha", 0), ("core|||beta", 1)],
    )
    conn.commit()
    return conn


class TestLibraryScopes:
    def test_counts_every_scope(self, mirror_heavy):
        scopes = library_scopes(mirror_heavy)
        assert scopes == LibraryScopes(
            mirror=13, curated=3, embedded=2, sonic=1, liked=4, rotation=2
        )

    def test_orphan_side_rows_never_inflate(self, mirror_heavy):
        """A context/embedding row whose track was deleted must not count —
        the JOIN doctrine that keeps facet denominators honest."""
        conn = mirror_heavy
        conn.execute("PRAGMA foreign_keys=OFF;")
        conn.execute("DELETE FROM tracks WHERE track_id = 'core|||gamma'")
        scopes = library_scopes(conn)
        assert scopes.curated == 2  # gamma's orphan context row is invisible
        assert scopes.mirror == 12

    def test_payload_fragment_is_flat_ints(self, mirror_heavy):
        payload = scopes_payload(library_scopes(mirror_heavy))
        assert payload == {
            "mirror": 13,
            "curated": 3,
            "embedded": 2,
            "sonic": 1,
            "liked": 4,
            "rotation": 2,
        }

    def test_empty_database(self):
        scopes = library_scopes(_connect())
        assert scopes == LibraryScopes(0, 0, 0, 0, 0, 0)


class TestCaptions:
    def test_mirror_caption_pins_the_canonical_string(self, mirror_heavy):
        scopes = library_scopes(mirror_heavy)
        assert (
            mirror_scope_caption(scopes)
            == "library mirror · 13 tracks · 23% enriched — /enrich grows this"
        )

    def test_curated_caption(self, mirror_heavy):
        assert (
            curated_scope_caption(library_scopes(mirror_heavy))
            == "curated core · 3 enriched tracks"
        )

    def test_counts_group_thousands(self):
        scopes = LibraryScopes(
            mirror=15468, curated=1244, embedded=1271, sonic=509, liked=4750, rotation=180
        )
        assert (
            mirror_scope_caption(scopes)
            == "library mirror · 15,468 tracks · 8% enriched — /enrich grows this"
        )
        assert curated_scope_caption(scopes) == "curated core · 1,244 enriched tracks"
        assert fmt_n(15468) == "15,468"

    def test_enriched_pct_never_rounds_a_core_to_zero(self):
        assert enriched_pct_str(LibraryScopes(1000, 3, 0, 0, 0, 0)) == "<1%"
        assert enriched_pct_str(LibraryScopes(0, 0, 0, 0, 0, 0)) == "0%"
        assert enriched_pct_str(LibraryScopes(10, 0, 0, 0, 0, 0)) == "0%"
        assert enriched_pct_str(LibraryScopes(13, 3, 0, 0, 0, 0)) == "23%"

    def test_plays_caption_names_window_start_never_lifetime(self):
        assert (
            plays_scope_caption("2026-07-24T14:41:03.773Z")
            == "plays · since 2026-07-24 · grows via /listen-sync"
        )
        assert plays_scope_caption(None) == "plays · none yet — /listen-sync starts the ledger"
        assert plays_scope_caption("") == "plays · none yet — /listen-sync starts the ledger"
