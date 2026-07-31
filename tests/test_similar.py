"""Tests for /similar — local more-like-this over stored embeddings.

KNN correctness runs on small hand-seeded matrices (the deterministic
``sentence_transformers`` stub from conftest embeds any on-the-fly text), the
--to write path runs against the mock Spotify fixture, and everything stays
offline against an in-memory SQLite database.
"""

import argparse
import json
import sqlite3
from datetime import datetime

import pytest
from rich.table import Table

import ui
from main import PlaylistCLI, dispatch_command
from models import Song, track_id_for
from song_store import SongStore
from storage.migrations import ensure_schema
from storage.repos import Repositories


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


@pytest.fixture
def store(cli):
    return cli.db


def _add(store, artist, name, embedding=None, uri=None):
    song = Song(
        id=track_id_for(artist, name),
        name=name,
        artist=artist,
        embedding=embedding,
        spotify_uri=uri,
        first_added=datetime(2024, 1, 1),
    )
    store.add_song(song)
    return song.id


def _args(**over):
    base = dict(query=["a|||one"], limit=10, to_playlist=None, json=False)
    base.update(over)
    return argparse.Namespace(**base)


# ---- SongStore.rank_similar (the scored top-N KNN) ----
class TestRankSimilar:
    def test_orders_by_cosine_best_first(self, store):
        _add(store, "A", "one", [1.0, 0.0, 0.0, 0.0])
        # Unit vector at cosine 0.9 to the query.
        _add(store, "B", "two", [0.9, 0.4358898943540674, 0.0, 0.0])
        _add(store, "C", "three", [0.0, 1.0, 0.0, 0.0])
        _add(store, "D", "four", [-1.0, 0.0, 0.0, 0.0])

        ranked = store.rank_similar([1.0, 0.0, 0.0, 0.0], limit=3)
        assert [track_id for track_id, _ in ranked] == ["a|||one", "b|||two", "c|||three"]
        scores = dict(ranked)
        assert scores["a|||one"] == pytest.approx(1.0, abs=1e-5)
        assert scores["b|||two"] == pytest.approx(0.9, abs=1e-5)
        assert scores["c|||three"] == pytest.approx(0.0, abs=1e-5)

    def test_scores_are_scale_invariant_cosine(self, store):
        # Same direction, wildly different magnitude: cosine must still be 1.
        _add(store, "A", "one", [10.0, 0.0, 0.0, 0.0])
        ranked = store.rank_similar([0.2, 0.0, 0.0, 0.0], limit=1)
        assert ranked[0][0] == "a|||one"
        assert ranked[0][1] == pytest.approx(1.0, abs=1e-5)

    def test_exclude_ids_and_limit(self, store):
        _add(store, "A", "one", [1.0, 0.0, 0.0, 0.0])
        _add(store, "B", "two", [0.9, 0.4358898943540674, 0.0, 0.0])
        _add(store, "C", "three", [0.0, 1.0, 0.0, 0.0])

        ranked = store.rank_similar([1.0, 0.0, 0.0, 0.0], limit=2, exclude_ids={"a|||one"})
        assert [track_id for track_id, _ in ranked] == ["b|||two", "c|||three"]

    def test_dimension_mismatch_rows_are_skipped(self, store):
        _add(store, "A", "one", [1.0, 0.0, 0.0, 0.0])
        _add(store, "B", "eight", [1.0] * 8)

        ranked = store.rank_similar([1.0, 0.0, 0.0, 0.0], limit=10)
        assert [track_id for track_id, _ in ranked] == ["a|||one"]

    def test_zero_vectors_never_match(self, store):
        _add(store, "A", "one", [0.0, 0.0, 0.0, 0.0])
        _add(store, "B", "two", [1.0, 0.0, 0.0, 0.0])

        ranked = store.rank_similar([1.0, 0.0, 0.0, 0.0], limit=10)
        assert [track_id for track_id, _ in ranked] == ["b|||two"]
        # A zero query can't rank anything.
        assert store.rank_similar([0.0, 0.0, 0.0, 0.0], limit=10) == []

    def test_empty_table_and_non_positive_limit(self, store):
        assert store.rank_similar([1.0, 0.0], limit=10) == []
        _add(store, "A", "one", [1.0, 0.0])
        assert store.rank_similar([1.0, 0.0], limit=0) == []


# ---- PlaylistCLI.similar_tracks ----
class TestSimilarTracks:
    def test_track_seed_uses_stored_embedding_and_excludes_itself(self, cli, store):
        # The seed's stored vector is nothing like any stub text embedding,
        # so ranking by it proves the stored embedding was used.
        _add(store, "A", "one", [1.0, 0.0, 0.0, 0.0])
        _add(store, "B", "two", [0.9, 0.4358898943540674, 0.0, 0.0])
        _add(store, "C", "three", [0.0, 1.0, 0.0, 0.0])

        payload = cli.similar_tracks("a|||one", limit=2)
        assert payload["seed"] == {"track_id": "a|||one", "label": "one — A"}
        assert [r["track_id"] for r in payload["results"]] == ["b|||two", "c|||three"]
        assert payload["results"][0]["similarity"] == pytest.approx(0.9, abs=1e-4)

    def test_track_seed_lookup_is_case_insensitive(self, cli, store):
        _add(store, "A", "one", [1.0, 0.0, 0.0, 0.0])
        _add(store, "B", "two", [0.9, 0.4358898943540674, 0.0, 0.0])

        payload = cli.similar_tracks("A|||ONE", limit=1)
        assert payload["seed"]["track_id"] == "a|||one"
        assert [r["track_id"] for r in payload["results"]] == ["b|||two"]

    def test_track_seed_without_stored_embedding_falls_back_to_lexical(self, cli, store):
        _add(store, "A", "one")  # seed: no embedding row
        # Neighbor embedded with exactly the seed's lexical text -> cosine 1.
        _add(store, "B", "twin", store.embed_texts(["one by A"])[0])
        _add(store, "C", "three", store.embed_texts(["three by C"])[0])

        payload = cli.similar_tracks("a|||one", limit=2)
        assert payload["results"][0]["track_id"] == "b|||twin"
        assert payload["results"][0]["similarity"] == pytest.approx(1.0, abs=1e-4)
        assert "a|||one" not in {r["track_id"] for r in payload["results"]}

    def test_free_text_query_is_embedded_on_the_fly(self, cli, store):
        _add(store, "B", "two", store.embed_texts(["two by B"])[0])
        _add(store, "C", "three", store.embed_texts(["three by C"])[0])

        payload = cli.similar_tracks("two by B", limit=1)
        assert payload["seed"] is None
        assert payload["results"][0]["track_id"] == "b|||two"
        assert payload["results"][0]["similarity"] == pytest.approx(1.0, abs=1e-4)

    def test_basis_reflects_track_context_row(self, cli, store):
        _add(store, "A", "one", [1.0, 0.0, 0.0, 0.0])
        _add(store, "B", "two", [0.9, 0.4358898943540674, 0.0, 0.0])
        _add(store, "C", "three", [0.0, 1.0, 0.0, 0.0])
        cli.repos.context.upsert(
            {
                "track_id": "b|||two",
                "context_text": "dream pop, hazy, 1988",
                "generated_at": "2026-01-01T00:00:00",
            }
        )
        cli.repos.conn.commit()

        payload = cli.similar_tracks("a|||one", limit=2)
        basis = {r["track_id"]: r["basis"] for r in payload["results"]}
        assert basis == {"b|||two": "context", "c|||three": "title"}

    def test_no_embeddings_yields_empty_results(self, cli, store):
        _add(store, "A", "one")  # track exists, nothing embedded
        payload = cli.similar_tracks("a|||one", limit=5)
        assert payload["results"] == []


# ---- the /similar command surface ----
class TestSimilarCommand:
    def test_table_carries_similarity_basis_and_spotify_links(self, cli, store):
        _add(store, "A", "one", [1.0, 0.0, 0.0, 0.0])
        _add(store, "B", "two", [0.9, 0.4358898943540674, 0.0, 0.0], uri="spotify:track:abc")
        _add(store, "C", "three", [0.0, 1.0, 0.0, 0.0])

        captured = []
        ui.set_output_sink(captured.append)
        try:
            rc = dispatch_command(cli, "similar", _args(limit=2))
        finally:
            ui.set_output_sink(None)
        assert rc == 0

        results_table = [r for r in captured if isinstance(r, Table)][-1]
        headers = [column.header for column in results_table.columns]
        assert headers == ["#", "Song", "Artist", "Sim", "Basis"]
        linked_cell, plain_cell = list(results_table.columns[1].cells)
        # Visible text unchanged; a known Spotify identity adds an OSC 8 link.
        assert linked_cell.plain == "two"
        assert [
            span.style.link for span in linked_cell.spans if getattr(span.style, "link", None)
        ] == ["https://open.spotify.com/track/abc"]
        assert not [span for span in plain_cell.spans if getattr(span.style, "link", None)]
        assert list(results_table.columns[3].cells)[0].plain == "0.90"
        assert [c.plain for c in results_table.columns[4].cells] == ["title", "title"]

    def test_to_writes_through_the_undoable_playlist_path(self, cli, store, mock_spotify_manager):
        cli._spotify = mock_spotify_manager
        _add(store, "A", "one", [1.0, 0.0, 0.0, 0.0])
        _add(store, "B", "two", [0.9, 0.4358898943540674, 0.0, 0.0])
        _add(store, "C", "three", [0.0, 1.0, 0.0, 0.0])

        rc = dispatch_command(cli, "similar", _args(limit=1, to_playlist="Test Playlist"))
        assert rc == 0
        mock_spotify_manager.append_to_playlist.assert_called_once()
        playlist_name, songs = mock_spotify_manager.append_to_playlist.call_args[0]
        assert playlist_name == "Test Playlist"
        assert [song.id for song in songs] == ["b|||two"]
        # The write snapshotted the prior contents for /undo.
        assert cli._undo_stack and cli._undo_stack[-1]["playlist"] == "Test Playlist"
        assert len(cli._undo_stack[-1]["tracks"]) == 2

    def test_json_mode_emits_machine_readable_payload(self, cli, store, capsys):
        _add(store, "A", "one", [1.0, 0.0, 0.0, 0.0])
        _add(store, "B", "two", [0.9, 0.4358898943540674, 0.0, 0.0])

        rc = dispatch_command(cli, "similar", _args(limit=1, json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["seed"]["track_id"] == "a|||one"
        assert [r["track_id"] for r in payload["results"]] == ["b|||two"]
        assert payload["results"][0]["basis"] == "title"
        assert payload["results"][0]["similarity"] == pytest.approx(0.9, abs=1e-4)

    def test_no_neighbors_returns_error_and_skips_write(self, cli, store, mock_spotify_manager):
        cli._spotify = mock_spotify_manager
        _add(store, "A", "one")  # nothing embedded anywhere

        rc = dispatch_command(cli, "similar", _args(to_playlist="Test Playlist"))
        assert rc == 1
        mock_spotify_manager.append_to_playlist.assert_not_called()
        assert cli._undo_stack == []
