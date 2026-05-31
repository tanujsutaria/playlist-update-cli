"""Tests for the SQLite-backed SongStore adapter.

Uses an in-memory SQLite database with the full schema applied. The
``sentence_transformers`` stub installed by conftest makes EmbeddingModel
return small deterministic vectors, so embedding-dependent methods run without
the real (heavyweight) encoder.
"""

import sqlite3
from datetime import datetime

import pytest

from models import Song, track_id_for
from song_store import SongStore
from storage.migrations import ensure_schema
from storage.repos import Repositories


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return SongStore(Repositories(conn), model_name="all-mpnet-base-v2")


def _song(artist, name, embedding=None, uri=None):
    return Song(
        id=track_id_for(artist, name),
        name=name,
        artist=artist,
        embedding=embedding,
        spotify_uri=uri,
        first_added=datetime(2024, 1, 1),
    )


def test_track_id_helper():
    assert track_id_for("Artist", "Song") == "artist|||song"


def test_add_and_get_song(store):
    song = _song("Artist One", "Song One", embedding=[0.1] * 8, uri="spotify:track:abc")

    assert store.add_song(song) is True
    # Re-adding the same track returns False (already existed).
    assert store.add_song(song) is False

    fetched = store.get_song_by_id(song.id)
    assert fetched is not None
    assert fetched.id == song.id
    assert fetched.name == "Song One"
    assert fetched.artist == "Artist One"
    assert fetched.spotify_uri == "spotify:track:abc"
    assert fetched.first_added == datetime(2024, 1, 1)


def test_get_song_by_id_missing(store):
    assert store.get_song_by_id("nope|||nope") is None


def test_get_all_songs(store):
    store.add_song(_song("A", "one", embedding=[0.2] * 8))
    store.add_song(_song("B", "two", embedding=[0.3] * 8))

    songs = store.get_all_songs()
    assert len(songs) == 2
    assert {s.id for s in songs} == {"a|||one", "b|||two"}


def test_remove_song(store):
    song = _song("A", "one", embedding=[0.2] * 8)
    store.add_song(song)

    assert store.remove_song(song.id) is True
    assert store.get_song_by_id(song.id) is None
    # Embedding row is gone too.
    assert store.repos.embeddings.get(song.id) is None
    # Removing a non-existent track returns False.
    assert store.remove_song(song.id) is False


def test_get_stats(store):
    store.add_song(_song("A", "one", embedding=[0.2] * 8))
    store.add_song(_song("B", "two", embedding=[0.3] * 8))

    stats = store.get_stats()
    assert stats["total_songs"] == 2
    assert stats["embedding_dimensions"] == 8
    assert "storage_size_mb" in stats


def test_generate_embedding(store):
    vector = store.generate_embedding(_song("A", "one"))
    assert vector.ndim == 1
    assert len(vector) > 0


def test_find_similar_songs(store):
    base = _song("A", "one")
    base.embedding = store.generate_embedding(base).tolist()
    store.add_song(base)

    other = _song("B", "two")
    other.embedding = store.generate_embedding(other).tolist()
    store.add_song(other)

    # threshold=0.0 guarantees at least the other song matches.
    results = store.find_similar_songs(_song("A", "one"), k=5, threshold=0.0)
    assert all(isinstance(s, Song) for s in results)
    assert base.id not in {s.id for s in results}


def test_save_state_is_noop_commit(store):
    # Should not raise; just flushes pending writes.
    store._save_state()
