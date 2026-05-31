"""Idempotent migrator from the legacy pickle store into the SQLite v4 schema.

Loads ``data/embeddings/songs.pkl`` (``dict[str, models.Song]``) and the
``data/history/*.pkl`` ``PlaylistHistory`` files and writes them into the
``artists``/``tracks``/``track_embeddings`` and
``playlists``/``rotation_generations``/``generation_tracks`` tables.

The legacy embeddings are 384-dim TF-IDF vectors and are intentionally NOT
copied. Instead tracks are RE-EMBEDDED with the canonical SentenceTransformer
model so the stored ``model_name`` matches the search pipeline's model — if it
did not, ``SearchPipeline._ensure_model_consistency`` would delete every row in
``track_embeddings`` on the next run.

Re-runs are no-ops: deterministic uuid5 generation ids, ON CONFLICT upserts and
the ``UNIQUE(playlist_id, generation_index)`` guard make the migration
idempotent. It never deletes any legacy pickle/npy file (that is T10).
"""

from __future__ import annotations

import json
import pickle
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

from .repos import Repositories
from .vectors import encode_vector, vector_norm

DEFAULT_MODEL_NAME = "all-mpnet-base-v2"
_EMBED_BATCH_SIZE = 64


class Embedder(Protocol):
    """Minimal embedding interface (matches ``nextgen.embeddings.EmbeddingModel``)."""

    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", name.lower())


def _load_pickle(path: Path) -> Any:
    with path.open("rb") as handle:
        return pickle.load(handle)


def _track_text(song: Any) -> str:
    return f"{song.name} by {song.artist}"


def migrate_legacy(
    repos: Repositories,
    *,
    model_name: str,
    data_dir: str,
    embedder: Optional[Embedder] = None,
    reembed: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Migrate the legacy pickle store into the connected SQLite v4 database.

    ``repos`` must already be connected and migrated to v4 by the caller.
    ``embedder`` defaults to ``EmbeddingModel(model_name)`` (lazily imported so
    tests can inject a fake without loading sentence-transformers). When
    ``dry_run`` is set the transaction is rolled back at the end and nothing is
    persisted, but the report counts still reflect what would have happened.
    """
    conn = repos.conn
    base = Path(data_dir)
    now = _now_iso()

    report: Dict[str, Any] = {
        "songs_loaded": 0,
        "tracks_upserted": 0,
        "artists_upserted": 0,
        "embeddings_written": 0,
        "embeddings_skipped": 0,
        "embedding_dim": None,
        "model_name": model_name,
        "playlists": 0,
        "generations": 0,
        "generation_tracks": 0,
        "orphan_track_ids": [],
    }

    # --- Phase 1: songs -> artists + tracks ---------------------------------
    songs: Dict[str, Any] = {}
    songs_path = base / "embeddings" / "songs.pkl"
    if songs_path.exists():
        loaded = _load_pickle(songs_path)
        if isinstance(loaded, dict):
            songs = loaded
    report["songs_loaded"] = len(songs)

    seen_artists: set[str] = set()
    for song in songs.values():
        artist_id = song.artist.lower()
        if artist_id not in seen_artists:
            repos.artists.upsert(
                artist_id=artist_id,
                name=song.artist,
                genres_json=json.dumps([]),
                updated_at=now,
            )
            seen_artists.add(artist_id)
            report["artists_upserted"] += 1

        created_at = song.first_added.isoformat() if song.first_added else now
        repos.tracks.upsert(
            {
                "track_id": song.id,
                "spotify_id": song.spotify_uri,
                "name": song.name,
                "artist_id": artist_id,
                "album_name": None,
                "release_date": None,
                "duration_ms": None,
                "explicit": None,
                "popularity": None,
                "spotify_url": None,
                "status": "candidate",
                "last_decision": None,
                "decision_reason": None,
                "created_at": created_at,
                "updated_at": now,
            }
        )
        report["tracks_upserted"] += 1

    if not dry_run:
        conn.commit()

    # --- Phase 2: re-embed tracks with the canonical model ------------------
    if reembed and songs:
        if embedder is None:
            from nextgen.embeddings import EmbeddingModel  # lazy: avoid ST import in tests

            embedder = EmbeddingModel(model_name)

        items = list(songs.values())
        for start in range(0, len(items), _EMBED_BATCH_SIZE):
            batch = items[start : start + _EMBED_BATCH_SIZE]
            texts = [_track_text(song) for song in batch]
            vectors = embedder.embed(texts)
            for song, vec in zip(batch, vectors):
                dim = len(vec)
                existing = repos.embeddings.get(song.id)
                if (
                    existing is not None
                    and existing.get("model_name") == model_name
                    and existing.get("embedding_dim") == dim
                ):
                    report["embeddings_skipped"] += 1
                    continue
                repos.embeddings.upsert(
                    {
                        "track_id": song.id,
                        "model_name": model_name,
                        "embedding_blob": encode_vector(vec),
                        "embedding_dim": dim,
                        "embedding_norm": vector_norm(vec),
                        "strict_ratio": None,
                        "created_at": now,
                    }
                )
                report["embeddings_written"] += 1
                if report["embedding_dim"] is None:
                    report["embedding_dim"] = dim

        if not dry_run:
            conn.commit()

    # --- Phase 3: history pkl -> rotation tables ----------------------------
    orphans: set[str] = set()
    history_dir = base / "history"
    if history_dir.exists():
        for history_path in sorted(history_dir.glob("*.pkl")):
            history = _load_pickle(history_path)
            slug = _slugify(history.name)
            repos.playlists.upsert(
                playlist_id=slug,
                name=history.name,
                spotify_playlist_id=history.playlist_id,
                current_generation=history.current_generation,
                created_at=now,
                updated_at=now,
            )
            report["playlists"] += 1

            for gi, gen in enumerate(history.generations):
                generation_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}|{gi}").hex
                generation_id = repos.rotation_generations.upsert(
                    generation_id, slug, gi, created_at=now
                )
                report["generations"] += 1
                for pos, sid in enumerate(gen):
                    if repos.tracks.get(sid) is None:
                        orphans.add(sid)
                        continue
                    repos.generation_tracks.add(generation_id, sid, pos)
                    report["generation_tracks"] += 1

        if not dry_run:
            conn.commit()

    report["orphan_track_ids"] = sorted(orphans)

    if dry_run:
        conn.rollback()

    return report
