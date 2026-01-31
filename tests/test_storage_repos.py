from __future__ import annotations

from pathlib import Path

from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import encode_vector


def test_repos_roundtrip(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    conn = db.connect()
    ensure_schema(conn)
    repos = Repositories(conn)

    repos.artists.upsert(
        artist_id="artist-1",
        name="Artist One",
        genres_json='["indie"]',
        popularity=42,
        updated_at="2026-01-31T00:00:00Z",
    )

    repos.tracks.upsert({
        "track_id": "artist-1|||song-a",
        "spotify_id": "spotify:track:abc",
        "name": "song-a",
        "artist_id": "artist-1",
        "album_name": "album-a",
        "release_date": "2020-01-01",
        "duration_ms": 123456,
        "explicit": 0,
        "popularity": 10,
        "spotify_url": "https://open.spotify.com/track/abc",
        "status": "candidate",
        "last_decision": None,
        "decision_reason": None,
        "created_at": "2026-01-31T00:00:00Z",
        "updated_at": "2026-01-31T00:00:00Z",
    })

    repos.context.upsert({
        "track_id": "artist-1|||song-a",
        "context_text": "rich context",
        "strict_text": "strict",
        "lenient_text": "lenient",
        "fields_json": "{}",
        "sources_json": "[]",
        "strict_ratio": 0.8,
        "context_version": "v1",
        "generated_at": "2026-01-31T00:00:00Z",
    })

    repos.embeddings.upsert({
        "track_id": "artist-1|||song-a",
        "model_name": "test-model",
        "embedding_blob": encode_vector([0.1, 0.2, 0.3]),
        "embedding_dim": 3,
        "embedding_norm": 1.0,
        "strict_ratio": 0.8,
        "created_at": "2026-01-31T00:00:00Z",
    })

    repos.queries.upsert({
        "query_hash": "hash-1",
        "query_text": "dreamy indie",
        "constraints_json": "{}",
        "embedding_blob": encode_vector([0.2, 0.1]),
        "embedding_dim": 2,
        "model_name": "test-model",
        "created_at": "2026-01-31T00:00:00Z",
        "last_used_at": "2026-01-31T00:00:00Z",
    })

    repos.runs.insert({
        "run_id": "run-1",
        "query_hash": "hash-1",
        "provider": "provider-a",
        "expanded": 0,
        "status": "ok",
        "error": None,
        "started_at": "2026-01-31T00:00:00Z",
        "finished_at": "2026-01-31T00:00:01Z",
        "results_count": 1,
    })

    repos.candidates.upsert({
        "run_id": "run-1",
        "track_id": "artist-1|||song-a",
        "rank": 1,
        "score_text": 0.9,
        "score_audio": None,
        "score_final": 0.9,
        "strict_ratio": 0.8,
        "lenient_ratio": 0.2,
        "sources_count": 2,
    })

    repos.sources.upsert({
        "source_id": "source-1",
        "track_id": "artist-1|||song-a",
        "url": "https://example.com",
        "title": "Example",
        "snippet": "Snippet text",
        "provider": "provider-a",
        "is_strict": 1,
        "retrieved_at": "2026-01-31T00:00:00Z",
    })

    repos.listen_events.upsert({
        "event_id": "event-1",
        "track_id": "artist-1|||song-a",
        "spotify_id": "spotify:track:abc",
        "played_at": "2026-01-31T01:00:00Z",
        "source": "recently_played",
        "created_at": "2026-01-31T01:00:01Z",
    })

    assert repos.artists.get("artist-1")["name"] == "Artist One"
    assert repos.tracks.get("artist-1|||song-a")["spotify_id"] == "spotify:track:abc"
    assert repos.context.get("artist-1|||song-a")["strict_text"] == "strict"
    assert repos.embeddings.get("artist-1|||song-a")["embedding_dim"] == 3
    assert repos.queries.get("hash-1")["query_text"] == "dreamy indie"
    assert repos.runs.get("run-1")["status"] == "ok"
    assert repos.candidates.list_by_run("run-1")[0]["rank"] == 1
    assert repos.sources.list_by_track("artist-1|||song-a")[0]["title"] == "Example"
    assert repos.listen_events.list_by_track("artist-1|||song-a")[0]["source"] == "recently_played"
