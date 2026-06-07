from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


def _row_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


@dataclass
class ArtistsRepo:
    conn: sqlite3.Connection

    def upsert(
        self,
        artist_id: str,
        name: str,
        genres_json: Optional[str] = None,
        popularity: Optional[int] = None,
        updated_at: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO artists (artist_id, name, genres_json, popularity, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(artist_id) DO UPDATE SET
              name=excluded.name,
              genres_json=excluded.genres_json,
              popularity=excluded.popularity,
              updated_at=excluded.updated_at;
            """,
            (artist_id, name, genres_json, popularity, updated_at),
        )

    def get(self, artist_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM artists WHERE artist_id = ?;",
            (artist_id,),
        ).fetchone()
        return _row_dict(row)


@dataclass
class TracksRepo:
    conn: sqlite3.Connection

    def upsert(self, payload: Dict[str, Any]) -> None:
        columns = [
            "track_id",
            "spotify_id",
            "name",
            "artist_id",
            "album_name",
            "release_date",
            "duration_ms",
            "explicit",
            "popularity",
            "spotify_url",
            "status",
            "last_decision",
            "decision_reason",
            "created_at",
            "updated_at",
        ]
        values = [payload.get(col) for col in columns]
        self.conn.execute(
            f"""
            INSERT INTO tracks ({", ".join(columns)})
            VALUES ({", ".join(["?"] * len(columns))})
            ON CONFLICT(track_id) DO UPDATE SET
              spotify_id=excluded.spotify_id,
              name=excluded.name,
              artist_id=excluded.artist_id,
              album_name=excluded.album_name,
              release_date=excluded.release_date,
              duration_ms=excluded.duration_ms,
              explicit=excluded.explicit,
              popularity=excluded.popularity,
              spotify_url=excluded.spotify_url,
              status=excluded.status,
              last_decision=excluded.last_decision,
              decision_reason=excluded.decision_reason,
              created_at=excluded.created_at,
              updated_at=excluded.updated_at;
            """,
            values,
        )

    def get(self, track_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM tracks WHERE track_id = ?;",
            (track_id,),
        ).fetchone()
        return _row_dict(row)

    def get_by_spotify_id(self, spotify_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM tracks WHERE spotify_id = ?;",
            (spotify_id,),
        ).fetchone()
        return _row_dict(row)

    def update_status(
        self,
        track_id: str,
        status: str,
        decision_reason: Optional[str],
        updated_at: Optional[str],
    ) -> None:
        self.conn.execute(
            """
            UPDATE tracks
            SET status = ?, last_decision = ?, decision_reason = ?, updated_at = ?
            WHERE track_id = ?;
            """,
            (status, status, decision_reason, updated_at, track_id),
        )


@dataclass
class TrackContextRepo:
    conn: sqlite3.Connection

    def upsert(self, payload: Dict[str, Any]) -> None:
        columns = [
            "track_id",
            "context_text",
            "strict_text",
            "lenient_text",
            "fields_json",
            "sources_json",
            "strict_ratio",
            "context_version",
            "generated_at",
        ]
        values = [payload.get(col) for col in columns]
        self.conn.execute(
            f"""
            INSERT INTO track_context ({", ".join(columns)})
            VALUES ({", ".join(["?"] * len(columns))})
            ON CONFLICT(track_id) DO UPDATE SET
              context_text=excluded.context_text,
              strict_text=excluded.strict_text,
              lenient_text=excluded.lenient_text,
              fields_json=excluded.fields_json,
              sources_json=excluded.sources_json,
              strict_ratio=excluded.strict_ratio,
              context_version=excluded.context_version,
              generated_at=excluded.generated_at;
            """,
            values,
        )

    def get(self, track_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM track_context WHERE track_id = ?;",
            (track_id,),
        ).fetchone()
        return _row_dict(row)


@dataclass
class TrackEmbeddingsRepo:
    conn: sqlite3.Connection

    def upsert(self, payload: Dict[str, Any]) -> None:
        columns = [
            "track_id",
            "model_name",
            "embedding_blob",
            "embedding_dim",
            "embedding_norm",
            "strict_ratio",
            "created_at",
        ]
        values = [payload.get(col) for col in columns]
        self.conn.execute(
            f"""
            INSERT INTO track_embeddings ({", ".join(columns)})
            VALUES ({", ".join(["?"] * len(columns))})
            ON CONFLICT(track_id) DO UPDATE SET
              model_name=excluded.model_name,
              embedding_blob=excluded.embedding_blob,
              embedding_dim=excluded.embedding_dim,
              embedding_norm=excluded.embedding_norm,
              strict_ratio=excluded.strict_ratio,
              created_at=excluded.created_at;
            """,
            values,
        )

    def get(self, track_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM track_embeddings WHERE track_id = ?;",
            (track_id,),
        ).fetchone()
        return _row_dict(row)


@dataclass
class TrackSonicRepo:
    conn: sqlite3.Connection

    def upsert(self, payload: Dict[str, Any]) -> None:
        columns = [
            "track_id",
            "mbid",
            "sonic_blob",
            "sonic_dim",
            "features_json",
            "source",
            "created_at",
        ]
        values = [payload.get(col) for col in columns]
        self.conn.execute(
            f"""
            INSERT INTO track_sonic ({", ".join(columns)})
            VALUES ({", ".join(["?"] * len(columns))})
            ON CONFLICT(track_id) DO UPDATE SET
              mbid=excluded.mbid,
              sonic_blob=excluded.sonic_blob,
              sonic_dim=excluded.sonic_dim,
              features_json=excluded.features_json,
              source=excluded.source,
              created_at=excluded.created_at;
            """,
            values,
        )

    def get(self, track_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM track_sonic WHERE track_id = ?;",
            (track_id,),
        ).fetchone()
        return _row_dict(row)


@dataclass
class QueriesRepo:
    conn: sqlite3.Connection

    def upsert(self, payload: Dict[str, Any]) -> None:
        columns = [
            "query_hash",
            "query_text",
            "constraints_json",
            "embedding_blob",
            "embedding_dim",
            "model_name",
            "created_at",
            "last_used_at",
        ]
        values = [payload.get(col) for col in columns]
        self.conn.execute(
            f"""
            INSERT INTO queries ({", ".join(columns)})
            VALUES ({", ".join(["?"] * len(columns))})
            ON CONFLICT(query_hash) DO UPDATE SET
              query_text=excluded.query_text,
              constraints_json=excluded.constraints_json,
              embedding_blob=excluded.embedding_blob,
              embedding_dim=excluded.embedding_dim,
              model_name=excluded.model_name,
              created_at=excluded.created_at,
              last_used_at=excluded.last_used_at;
            """,
            values,
        )

    def get(self, query_hash: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM queries WHERE query_hash = ?;",
            (query_hash,),
        ).fetchone()
        return _row_dict(row)


@dataclass
class SearchRunsRepo:
    conn: sqlite3.Connection

    def insert(self, payload: Dict[str, Any]) -> None:
        columns = [
            "run_id",
            "query_hash",
            "provider",
            "expanded",
            "status",
            "error",
            "started_at",
            "finished_at",
            "score_config_hash",
            "results_count",
            "summary",
        ]
        values = [payload.get(col) for col in columns]
        self.conn.execute(
            f"""
            INSERT INTO search_runs ({", ".join(columns)})
            VALUES ({", ".join(["?"] * len(columns))});
            """,
            values,
        )

    def update_status(self, run_id: str, status: str, error: Optional[str] = None) -> None:
        self.conn.execute(
            """
            UPDATE search_runs
            SET status = ?, error = ?
            WHERE run_id = ?;
            """,
            (status, error, run_id),
        )

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM search_runs WHERE run_id = ?;",
            (run_id,),
        ).fetchone()
        return _row_dict(row)


@dataclass
class SearchCandidatesRepo:
    conn: sqlite3.Connection

    def upsert(self, payload: Dict[str, Any]) -> None:
        columns = [
            "run_id",
            "track_id",
            "rank",
            "score_text",
            "score_audio",
            "score_final",
            "strict_ratio",
            "lenient_ratio",
            "sources_count",
            # INSERT-only: deliberately absent from the DO UPDATE SET clause below
            # so _rescore_cached_run (which re-upserts the same run without a
            # metrics payload) preserves the originally persisted metrics_json
            # instead of nulling it.
            "metrics_json",
        ]
        values = [payload.get(col) for col in columns]
        self.conn.execute(
            f"""
            INSERT INTO search_candidates ({", ".join(columns)})
            VALUES ({", ".join(["?"] * len(columns))})
            ON CONFLICT(run_id, track_id) DO UPDATE SET
              rank=excluded.rank,
              score_text=excluded.score_text,
              score_audio=excluded.score_audio,
              score_final=excluded.score_final,
              strict_ratio=excluded.strict_ratio,
              lenient_ratio=excluded.lenient_ratio,
              sources_count=excluded.sources_count;
            """,
            values,
        )

    def list_by_run(self, run_id: str) -> Iterable[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM search_candidates WHERE run_id = ? ORDER BY rank ASC;",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]


@dataclass
class TrackSourcesRepo:
    conn: sqlite3.Connection

    def upsert(self, payload: Dict[str, Any]) -> None:
        columns = [
            "source_id",
            "track_id",
            "url",
            "title",
            "snippet",
            "provider",
            "is_strict",
            "retrieved_at",
        ]
        values = [payload.get(col) for col in columns]
        self.conn.execute(
            f"""
            INSERT INTO track_sources ({", ".join(columns)})
            VALUES ({", ".join(["?"] * len(columns))})
            ON CONFLICT(source_id) DO UPDATE SET
              track_id=excluded.track_id,
              url=excluded.url,
              title=excluded.title,
              snippet=excluded.snippet,
              provider=excluded.provider,
              is_strict=excluded.is_strict,
              retrieved_at=excluded.retrieved_at;
            """,
            values,
        )

    def list_by_track(self, track_id: str) -> Iterable[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM track_sources WHERE track_id = ?;",
            (track_id,),
        ).fetchall()
        return [dict(row) for row in rows]


@dataclass
class ListenEventsRepo:
    conn: sqlite3.Connection

    def upsert(self, payload: Dict[str, Any]) -> None:
        columns = [
            "event_id",
            "track_id",
            "spotify_id",
            "played_at",
            "source",
            "created_at",
        ]
        values = [payload.get(col) for col in columns]
        self.conn.execute(
            f"""
            INSERT INTO listen_events ({", ".join(columns)})
            VALUES ({", ".join(["?"] * len(columns))})
            ON CONFLICT(event_id) DO UPDATE SET
              track_id=excluded.track_id,
              spotify_id=excluded.spotify_id,
              played_at=excluded.played_at,
              source=excluded.source,
              created_at=excluded.created_at;
            """,
            values,
        )

    def list_by_track(self, track_id: str) -> Iterable[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM listen_events
            WHERE track_id = ?
            ORDER BY played_at DESC;
            """,
            (track_id,),
        ).fetchall()
        return [dict(row) for row in rows]


@dataclass
class PlaylistsRepo:
    conn: sqlite3.Connection

    def upsert(
        self,
        playlist_id: str,
        name: str,
        spotify_playlist_id: Optional[str] = None,
        current_generation: int = 0,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO playlists (
              playlist_id, name, spotify_playlist_id, current_generation, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(playlist_id) DO UPDATE SET
              name=excluded.name,
              spotify_playlist_id=excluded.spotify_playlist_id,
              current_generation=excluded.current_generation,
              created_at=excluded.created_at,
              updated_at=excluded.updated_at;
            """,
            (playlist_id, name, spotify_playlist_id, current_generation, created_at, updated_at),
        )

    def get(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM playlists WHERE playlist_id = ?;",
            (playlist_id,),
        ).fetchone()
        return _row_dict(row)


@dataclass
class RotationGenerationsRepo:
    conn: sqlite3.Connection

    def upsert(
        self,
        generation_id: str,
        playlist_id: str,
        generation_index: int,
        created_at: Optional[str] = None,
    ) -> str:
        self.conn.execute(
            """
            INSERT INTO rotation_generations (
              generation_id, playlist_id, generation_index, created_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(playlist_id, generation_index) DO NOTHING;
            """,
            (generation_id, playlist_id, generation_index, created_at),
        )
        row = self.conn.execute(
            """
            SELECT generation_id FROM rotation_generations
            WHERE playlist_id = ? AND generation_index = ?;
            """,
            (playlist_id, generation_index),
        ).fetchone()
        if row is None:
            return generation_id
        return row["generation_id"] if isinstance(row, sqlite3.Row) else row[0]

    def list_by_playlist(self, playlist_id: str) -> Iterable[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM rotation_generations
            WHERE playlist_id = ?
            ORDER BY generation_index ASC;
            """,
            (playlist_id,),
        ).fetchall()
        return [dict(row) for row in rows]


@dataclass
class GenerationTracksRepo:
    conn: sqlite3.Connection

    def add(self, generation_id: str, track_id: str, position: int) -> None:
        self.conn.execute(
            """
            INSERT INTO generation_tracks (generation_id, track_id, position)
            VALUES (?, ?, ?)
            ON CONFLICT(generation_id, track_id) DO UPDATE SET
              position=excluded.position;
            """,
            (generation_id, track_id, position),
        )

    def list_by_generation(self, generation_id: str) -> Iterable[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM generation_tracks
            WHERE generation_id = ?
            ORDER BY position ASC;
            """,
            (generation_id,),
        ).fetchall()
        return [dict(row) for row in rows]


@dataclass
class Repositories:
    conn: sqlite3.Connection

    @property
    def artists(self) -> ArtistsRepo:
        return ArtistsRepo(self.conn)

    @property
    def tracks(self) -> TracksRepo:
        return TracksRepo(self.conn)

    @property
    def context(self) -> TrackContextRepo:
        return TrackContextRepo(self.conn)

    @property
    def embeddings(self) -> TrackEmbeddingsRepo:
        return TrackEmbeddingsRepo(self.conn)

    @property
    def sonic(self) -> TrackSonicRepo:
        return TrackSonicRepo(self.conn)

    @property
    def queries(self) -> QueriesRepo:
        return QueriesRepo(self.conn)

    @property
    def runs(self) -> SearchRunsRepo:
        return SearchRunsRepo(self.conn)

    @property
    def candidates(self) -> SearchCandidatesRepo:
        return SearchCandidatesRepo(self.conn)

    @property
    def sources(self) -> TrackSourcesRepo:
        return TrackSourcesRepo(self.conn)

    @property
    def listen_events(self) -> ListenEventsRepo:
        return ListenEventsRepo(self.conn)

    @property
    def playlists(self) -> PlaylistsRepo:
        return PlaylistsRepo(self.conn)

    @property
    def rotation_generations(self) -> RotationGenerationsRepo:
        return RotationGenerationsRepo(self.conn)

    @property
    def generation_tracks(self) -> GenerationTracksRepo:
        return GenerationTracksRepo(self.conn)
