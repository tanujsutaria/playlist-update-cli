from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence


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
            # v7 telemetry columns (optional, default None). On conflict these
            # are enrich-not-clobber via COALESCE: a GDPR re-import can add
            # ms_played to an event first seen via polling, but a None payload
            # never erases previously stored data.
            "ms_played",
            "skipped",
            "context_uri",
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
              created_at=excluded.created_at,
              ms_played=COALESCE(excluded.ms_played, listen_events.ms_played),
              skipped=COALESCE(excluded.skipped, listen_events.skipped),
              context_uri=COALESCE(excluded.context_uri, listen_events.context_uri);
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
class SyncStateRepo:
    conn: sqlite3.Connection

    def get(self, source: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM sync_state WHERE source = ?;",
            (source,),
        ).fetchone()
        return _row_dict(row)

    def set(
        self,
        source: str,
        cursor: Optional[str],
        last_synced_at: Optional[str],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sync_state (source, cursor, last_synced_at)
            VALUES (?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
              cursor=excluded.cursor,
              last_synced_at=excluded.last_synced_at;
            """,
            (source, cursor, last_synced_at),
        )


@dataclass
class SpotifyPlaylistsRepo:
    conn: sqlite3.Connection

    def upsert(
        self,
        spotify_playlist_id: str,
        name: str,
        owner: Optional[str] = None,
        is_owned: int = 0,
        snapshot_id: Optional[str] = None,
        total_tracks: Optional[int] = None,
        synced_at: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO spotify_playlists (
              spotify_playlist_id, name, owner, is_owned, snapshot_id, total_tracks, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(spotify_playlist_id) DO UPDATE SET
              name=excluded.name,
              owner=excluded.owner,
              is_owned=excluded.is_owned,
              snapshot_id=excluded.snapshot_id,
              total_tracks=excluded.total_tracks,
              synced_at=excluded.synced_at;
            """,
            (spotify_playlist_id, name, owner, is_owned, snapshot_id, total_tracks, synced_at),
        )

    def get(self, spotify_playlist_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM spotify_playlists WHERE spotify_playlist_id = ?;",
            (spotify_playlist_id,),
        ).fetchone()
        return _row_dict(row)

    def list_all(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM spotify_playlists ORDER BY name ASC;",
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_missing(self, keep_ids: Sequence[str]) -> int:
        """Delete playlists not in ``keep_ids``; return how many were deleted.

        playlist_tracks rows cascade (ON DELETE CASCADE) under foreign_keys=ON.
        """
        ids = list(keep_ids)
        if not ids:
            cur = self.conn.execute("DELETE FROM spotify_playlists;")
        else:
            placeholders = ", ".join(["?"] * len(ids))
            cur = self.conn.execute(
                f"DELETE FROM spotify_playlists WHERE spotify_playlist_id NOT IN ({placeholders});",
                ids,
            )
        return int(cur.rowcount)


@dataclass
class PlaylistTracksRepo:
    conn: sqlite3.Connection

    def replace_for_playlist(
        self,
        spotify_playlist_id: str,
        rows: Iterable[Dict[str, Any]],
    ) -> None:
        """DELETE then re-INSERT the playlist's membership rows.

        Not atomic by itself — the caller wraps this in a transaction/commit
        (e.g. ``Database.session``), per the repos-never-commit convention.
        """
        self.conn.execute(
            "DELETE FROM playlist_tracks WHERE spotify_playlist_id = ?;",
            (spotify_playlist_id,),
        )
        self.conn.executemany(
            """
            INSERT INTO playlist_tracks (
              spotify_playlist_id, track_id, added_at, position, synced_at
            )
            VALUES (?, ?, ?, ?, ?);
            """,
            [
                (
                    spotify_playlist_id,
                    row["track_id"],
                    row.get("added_at"),
                    row.get("position"),
                    row.get("synced_at"),
                )
                for row in rows
            ],
        )

    def list_for_playlist(self, spotify_playlist_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM playlist_tracks
            WHERE spotify_playlist_id = ?
            ORDER BY position ASC;
            """,
            (spotify_playlist_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def playlists_for_track(self, track_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT p.*
            FROM spotify_playlists p
            JOIN playlist_tracks pt ON pt.spotify_playlist_id = p.spotify_playlist_id
            WHERE pt.track_id = ?
            ORDER BY p.name ASC;
            """,
            (track_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM playlist_tracks;").fetchone()
        return int(row[0])


@dataclass
class LikedTracksRepo:
    conn: sqlite3.Connection

    def upsert(
        self,
        track_id: str,
        added_at: Optional[str] = None,
        synced_at: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO liked_tracks (track_id, added_at, synced_at)
            VALUES (?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
              added_at=excluded.added_at,
              synced_at=excluded.synced_at;
            """,
            (track_id, added_at, synced_at),
        )

    def prune_missing(self, keep_track_ids: Sequence[str]) -> int:
        """Delete liked rows not in ``keep_track_ids``; return how many were deleted."""
        ids = list(keep_track_ids)
        if not ids:
            cur = self.conn.execute("DELETE FROM liked_tracks;")
        else:
            placeholders = ", ".join(["?"] * len(ids))
            cur = self.conn.execute(
                f"DELETE FROM liked_tracks WHERE track_id NOT IN ({placeholders});",
                ids,
            )
        return int(cur.rowcount)

    def list_all(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM liked_tracks ORDER BY added_at DESC;",
        ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM liked_tracks;").fetchone()
        return int(row[0])


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

    @property
    def sync_state(self) -> SyncStateRepo:
        return SyncStateRepo(self.conn)

    @property
    def spotify_playlists(self) -> SpotifyPlaylistsRepo:
        return SpotifyPlaylistsRepo(self.conn)

    @property
    def playlist_tracks(self) -> PlaylistTracksRepo:
        return PlaylistTracksRepo(self.conn)

    @property
    def liked_tracks(self) -> LikedTracksRepo:
        return LikedTracksRepo(self.conn)
