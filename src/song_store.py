"""SQLite-backed song store.

``SongStore`` implements the interface the legacy ``DatabaseManager`` exposed to
its consumers (``get_all_songs`` / ``get_song_by_id`` / ``add_song`` /
``remove_song`` / ``find_similar_songs`` / ``generate_embedding`` /
``get_stats`` / ``_save_state``) but reads and writes the SQLite store via the
``Repositories`` layer. Embeddings use the canonical local ``EmbeddingModel``
(mpnet, 768-dim) rather than the old TF-IDF refit; the embed text is
``f"{song.name} by {song.artist}"`` so on-the-fly vectors match the ones written
during the schema-v4 migration.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from models import Song, track_id_for
from nextgen.embeddings import EmbeddingModel
from storage.repos import Repositories
from storage.vectors import decode_vector, encode_vector, vector_norm

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

_DEFAULT_MODEL = "all-mpnet-base-v2"


def _embed_text(song: Song) -> str:
    """The canonical text used to embed a song (matches the migration)."""
    return f"{song.name} by {song.artist}"


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class SongStore:
    """Adapter exposing the legacy DatabaseManager interface over SQLite repos."""

    def __init__(self, repos: Repositories, model_name: Optional[str] = None) -> None:
        self.repos = repos
        resolved = model_name or os.getenv("SEARCH_EMBEDDING_MODEL") or _DEFAULT_MODEL
        self.model_name: str = resolved
        self._model: Optional[EmbeddingModel] = None

    def _embedder(self) -> EmbeddingModel:
        if self._model is None:
            self._model = EmbeddingModel(self.model_name)
        return self._model

    def _row_to_song(self, row: Dict[str, Any]) -> Song:
        artist_id = row.get("artist_id") or ""
        artist_record = self.repos.artists.get(artist_id) if artist_id else None
        artist_name = artist_record["name"] if artist_record else artist_id
        return Song(
            id=row["track_id"],
            name=row["name"],
            artist=artist_name,
            embedding=None,
            spotify_uri=row.get("spotify_id"),
            first_added=_parse_datetime(row.get("created_at")),
        )

    def get_all_songs(self) -> List[Song]:
        rows = self.repos.conn.execute(
            "SELECT track_id, name, artist_id, spotify_id, created_at FROM tracks;"
        ).fetchall()
        return [self._row_to_song(dict(row)) for row in rows]

    def get_song_by_id(self, track_id: str) -> Optional[Song]:
        row = self.repos.tracks.get(track_id)
        if row is None:
            return None
        return self._row_to_song(row)

    def add_song(self, song: Song) -> bool:
        """Add a song to the store. Returns False if the track already existed."""
        existing = self.repos.tracks.get(song.id)
        already_existed = existing is not None

        now = datetime.now()
        created_at = (song.first_added or now).isoformat()
        artist_id = song.artist.lower()

        self.repos.artists.upsert(artist_id=artist_id, name=song.artist, updated_at=now.isoformat())
        self.repos.tracks.upsert(
            {
                "track_id": song.id,
                "spotify_id": song.spotify_uri,
                "name": song.name,
                "artist_id": artist_id,
                "status": "candidate",
                "created_at": created_at,
                "updated_at": now.isoformat(),
            }
        )

        if song.embedding is not None:
            values = [float(v) for v in song.embedding]
            self.repos.embeddings.upsert(
                {
                    "track_id": song.id,
                    "model_name": self.model_name,
                    "embedding_blob": encode_vector(values),
                    "embedding_dim": len(values),
                    "embedding_norm": vector_norm(values),
                    "created_at": now.isoformat(),
                }
            )

        self.repos.conn.commit()
        return not already_existed

    def remove_song(self, track_id: str) -> bool:
        """Remove a song (and its embedding). Returns whether a row existed."""
        existed = self.repos.tracks.get(track_id) is not None
        self.repos.conn.execute("DELETE FROM track_embeddings WHERE track_id = ?;", (track_id,))
        self.repos.conn.execute("DELETE FROM tracks WHERE track_id = ?;", (track_id,))
        self.repos.conn.commit()
        return existed

    def find_similar_songs(self, song: Song, k: int = 1, threshold: float = 0.9) -> List[Song]:
        """Find up to k songs whose stored embedding is cosine-similar to ``song``."""
        query = self._embedder().embed([_embed_text(song)])[0]
        query_norm = vector_norm(query)
        if query_norm == 0.0:
            return []

        rows = self.repos.conn.execute(
            "SELECT track_id, embedding_blob FROM track_embeddings WHERE track_id != ?;",
            (song.id,),
        ).fetchall()

        scored: List[tuple[float, str]] = []
        for row in rows:
            vector = decode_vector(row["embedding_blob"])
            norm = vector_norm(vector)
            if norm == 0.0:
                continue
            dot = sum(float(a) * float(b) for a, b in zip(query, vector))
            cosine = dot / (norm * query_norm)
            if cosine >= threshold:
                scored.append((cosine, row["track_id"]))

        scored.sort(key=lambda item: item[0], reverse=True)

        results: List[Song] = []
        for _, track_id in scored[:k]:
            match = self.get_song_by_id(track_id)
            if match is not None:
                results.append(match)
        return results

    def generate_embedding(self, song: Song) -> "np.ndarray":
        import numpy as np

        vector = self._embedder().embed([_embed_text(song)])[0]
        return np.array(vector, dtype=float)

    def get_stats(self) -> Dict[str, Any]:
        total_songs = int(self.repos.conn.execute("SELECT COUNT(*) FROM tracks;").fetchone()[0])
        dim_row = self.repos.conn.execute(
            "SELECT DISTINCT embedding_dim FROM track_embeddings LIMIT 1;"
        ).fetchone()
        embedding_dimensions = int(dim_row[0]) if dim_row and dim_row[0] is not None else 768

        storage_size_mb = 0.0
        path_row = self.repos.conn.execute("PRAGMA database_list;").fetchone()
        if path_row is not None:
            db_file = path_row["file"] if hasattr(path_row, "keys") else path_row[2]
            if db_file and os.path.exists(db_file):
                storage_size_mb = os.path.getsize(db_file) / 1024 / 1024

        return {
            "total_songs": total_songs,
            "embedding_dimensions": embedding_dimensions,
            "storage_size_mb": storage_size_mb,
        }

    def _save_state(self) -> None:
        """Legacy no-op shim: SQLite writes commit eagerly; just flush."""
        self.repos.conn.commit()


__all__ = ["SongStore", "track_id_for"]
