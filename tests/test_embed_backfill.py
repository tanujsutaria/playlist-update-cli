"""Tests for the /embed offline embedding backfill.

Runs against an in-memory SQLite database with the full schema applied; the
``sentence_transformers`` stub from conftest makes the embedder deterministic
(8-dim sha256-derived vectors), so the backfill runs offline and fast. The
live database is never touched (suite-wide TUNR_DB_PATH isolation).
"""

import hashlib
import sqlite3
from datetime import datetime

import numpy as np
import pytest

from main import PlaylistCLI, dispatch_command
from models import Song, track_id_for
from song_store import SongStore
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import decode_vector, encode_vector


@pytest.fixture
def cli():
    """A PlaylistCLI wired to a real in-memory store (no Spotify)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    repos = Repositories(conn)
    c = PlaylistCLI.__new__(PlaylistCLI)
    c._repos = repos
    c._db = SongStore(repos, model_name="all-mpnet-base-v2")
    c._spotify = None
    c._storage = None
    c._search_pipeline = None
    c._rotation_managers = {}
    c._undo_stack = []
    return c


def _add(cli, artist, name, embedding=None):
    song = Song(
        id=track_id_for(artist, name),
        name=name,
        artist=artist,
        embedding=embedding,
        spotify_uri=None,
        first_added=datetime(2024, 1, 1),
    )
    cli.db.add_song(song)
    return song.id


def _stub_vector(text):
    """The conftest sentence_transformers stub's normalized vector for text."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vec = np.frombuffer(digest[:8], dtype=np.uint8).astype(np.float64) / 255.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def _embedded_ids(cli):
    rows = cli.repos.conn.execute("SELECT track_id FROM track_embeddings").fetchall()
    return {row["track_id"] for row in rows}


class TestSelection:
    def test_backfills_only_missing_and_never_touches_existing(self, cli):
        kept = _add(cli, "Artist A", "one", embedding=[0.5] * 8)
        missing_b = _add(cli, "Artist B", "two")
        missing_c = _add(cli, "Artist C", "three")
        original_blob = cli.repos.embeddings.get(kept)["embedding_blob"]

        assert cli.embed_backfill() == 2

        assert _embedded_ids(cli) == {kept, missing_b, missing_c}
        # The pre-existing embedding (e.g. /enrich's context re-embed) is
        # byte-identical — the backfill never overwrites.
        assert cli.repos.embeddings.get(kept)["embedding_blob"] == original_blob
        assert cli.repos.embeddings.get(kept)["embedding_blob"] == encode_vector([0.5] * 8)

    def test_second_run_is_a_noop(self, cli):
        _add(cli, "Artist A", "one")
        assert cli.embed_backfill() == 1
        assert cli.embed_backfill() == 0

    def test_limit_bounds_the_run(self, cli):
        _add(cli, "A", "one")
        _add(cli, "B", "two")
        _add(cli, "C", "three")

        assert cli.embed_backfill(limit=1) == 1
        # track_id-ordered selection: only the first missing track embedded.
        assert _embedded_ids(cli) == {"a|||one"}


class TestWrittenRows:
    def test_embeds_canonical_name_by_artist_text(self, cli):
        track_id = _add(cli, "Artist A", "one")
        cli.embed_backfill()

        row = cli.repos.embeddings.get(track_id)
        stored = decode_vector(row["embedding_blob"])
        expected = _stub_vector("one by Artist A")
        np.testing.assert_allclose(stored, expected, rtol=1e-5, atol=1e-6)

    def test_row_metadata(self, cli):
        track_id = _add(cli, "Artist A", "one")
        cli.embed_backfill()

        row = cli.repos.embeddings.get(track_id)
        assert row["model_name"] == "all-mpnet-base-v2"
        assert row["embedding_dim"] == 8
        assert row["embedding_norm"] == pytest.approx(1.0, abs=1e-5)
        assert row["created_at"]


class TestDryRun:
    def test_dry_run_writes_nothing(self, cli):
        kept = _add(cli, "Artist A", "one", embedding=[0.5] * 8)
        _add(cli, "Artist B", "two")

        assert cli.embed_backfill(dry_run=True) == 0
        assert _embedded_ids(cli) == {kept}


class TestDispatchIntegration:
    def test_embed_command_dispatches_end_to_end(self, cli):
        import argparse

        _add(cli, "Artist A", "one")
        args = argparse.Namespace(limit=None, dry_run=False)
        rc = dispatch_command(cli, "embed", args)
        assert rc == 0
        assert _embedded_ids(cli) == {"artist a|||one"}
