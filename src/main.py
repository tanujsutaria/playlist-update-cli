import csv
import json
import logging
import os
import re
import shutil
import sys
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
from rich.logging import RichHandler

from config import AppConfig, env_flag, env_int
from models import Song, track_id_for
from nextgen.acoustic import backfill_sonic
from nextgen.embeddings import EmbeddingModel
from nextgen.enrich import enrich_tracks
from nextgen.pipeline import SearchPipeline, SearchResult
from nextgen.scoring import SearchScoreConfig
from rotation_manager import RotationManager
from scoring import PlaylistScoreConfig
from song_store import SongStore
from spotify_manager import (
    SpotifyManager,
    get_cached_token_info,
    refresh_cached_token,
    scope_error_hint,
)
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.sonic import describe_sonic
from storage.vectors import decode_vector, mean_vector, taste_centroid, vector_norm
from ui import (
    bar_chart,
    clear_preview,
    console,
    emit_json,
    info,
    json_output,
    key_value_table,
    preview_table,
    section,
    set_json_mode,
    sparkline,
    subsection,
    table,
    warning,
)

logger = logging.getLogger(__name__)


def configure_logging(handler: Optional[logging.Handler] = None) -> None:
    """Configure logging for CLI or interactive UI."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = []

    if handler is None:
        handler = RichHandler(
            console=console,
            show_time=True,
            show_level=True,
            show_path=False,
            rich_tracebacks=True,
            markup=False,
        )
    root_logger.addHandler(handler)


class PlaylistCLI:
    def __init__(self):
        # Get the project root directory
        project_root = Path(__file__).parent.parent
        load_dotenv(project_root / "config" / ".env")

        # Initialize managers as needed
        self._db = None
        self._spotify = None
        self._storage = None
        self._repos = None
        self._search_pipeline = None
        self._rotation_managers = {}
        self.last_search_results = None
        self.last_search_query = None
        self.last_search_summary = None
        self.last_search_metrics = None
        self.last_search_constraints = None
        self.last_search_expanded = False
        self.last_search_policy = None
        self.last_search_run_id = None
        self.last_search_track_ids = None
        self.last_search_cached = False
        self.last_search_handled = False
        # Session-scoped undo: each entry snapshots a playlist's tracks just
        # before a write, so /undo can restore it. Cleared on restart.
        self._undo_stack: List[Dict[str, Any]] = []

    @property
    def db(self) -> SongStore:
        """Lazy initialization of the SQLite-backed song store."""
        if self._db is None:
            logger.info("Initializing database manager...")
            self._db = SongStore(
                self.repos,
                model_name=os.getenv("SEARCH_EMBEDDING_MODEL", "all-mpnet-base-v2"),
            )
            logger.info(f"Loaded {len(self._db.get_all_songs())} songs from database")
        return self._db

    @property
    def spotify(self) -> SpotifyManager:
        """Lazy initialization of SpotifyManager"""
        if self._spotify is None:
            self._spotify = SpotifyManager()
        return self._spotify

    @property
    def storage(self) -> Database:
        if not hasattr(self, "_storage"):
            self._storage = None
        if self._storage is None:
            self._storage = Database()
            conn = self._storage.connect()
            ensure_schema(conn)
        return self._storage

    @property
    def repos(self) -> Repositories:
        if not hasattr(self, "_repos"):
            self._repos = None
        if self._repos is None:
            self._repos = Repositories(self.storage.connect())
        return self._repos

    @property
    def search_pipeline(self) -> SearchPipeline:
        if not hasattr(self, "_search_pipeline"):
            self._search_pipeline = None
        if self._search_pipeline is None:
            app_config = AppConfig.from_env()

            score_config = SearchScoreConfig(
                strict_weight=app_config.strict_weight,
                base_weight=app_config.base_weight,
                source_weight=app_config.source_weight,
                year_weight=app_config.year_weight,
                year_tolerance=app_config.year_tolerance,
                source_cap=app_config.source_cap,
            )
            self._search_pipeline = SearchPipeline(
                self.repos,
                model_name=app_config.model_name,
                strict_threshold=app_config.strict_threshold,
                lenient_threshold=app_config.lenient_threshold,
                score_config=score_config,
            )
        return self._search_pipeline

    def _reset_search_state(self) -> None:
        """Reset all search-related state to defaults."""
        self.last_search_results = None
        self.last_search_query = None
        self.last_search_summary = None
        self.last_search_metrics = None
        self.last_search_constraints = None
        self.last_search_expanded = False
        self.last_search_policy = None
        self.last_search_run_id = None
        self.last_search_track_ids = None
        self.last_search_cached = False
        # True once a /search invocation already handled its own follow-up
        # (via --to/--save), so the interactive UI skips the modal prompts.
        self.last_search_handled = False

    def _get_rotation_manager(self, playlist_name: str) -> RotationManager:
        """Get or create a rotation manager for a playlist"""
        if playlist_name not in self._rotation_managers:
            self._rotation_managers[playlist_name] = RotationManager(
                playlist_name=playlist_name,
                db=self.db,
                spotify=self.spotify,
                repos=self.repos,
            )
        return self._rotation_managers[playlist_name]

    def import_songs(self, file_path: str):
        """Import songs from a file into the database

        Supports both .txt and .csv files with format: song_name,artist_name
        Lines starting with # are treated as comments

        Validates that:
        1. The song exists in Spotify
        2. The artist has less than 1 million monthly listeners
        """
        logger.warning("Legacy import: consider using /ingest for Spotify-based sources.")
        path = Path(file_path)
        if not path.exists():
            logger.error(f"File not found: {file_path}")
            return

        if path.suffix.lower() not in [".txt", ".csv"]:
            logger.warning(f"File extension {path.suffix} not recognized. Expected .txt or .csv")
            logger.warning("Attempting to process file anyway...")

        # Initialize Spotify for validation
        try:
            # Ensure Spotify manager is initialized
            spotify = self.spotify
            logger.info("Spotify connection established for song validation")
        except Exception as e:
            logger.error(f"Failed to initialize Spotify for validation: {str(e)}")
            return

        # Track statistics
        stats = {
            "total": 0,
            "added": 0,
            "already_exists": 0,
            "not_found": 0,
            "popular_artist": 0,
            "error": 0,
        }

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        stats["total"] += 1

                        # Skip empty lines and comments
                        if not line.strip() or line.startswith("#"):
                            stats["total"] -= 1  # Don't count comments/empty lines
                            continue

                        # Split and take only first two columns (name, artist)
                        parts = [x.strip().lower() for x in line.split(",")]
                        if len(parts) < 2:
                            logger.warning(
                                f"Line {line_num}: Skipping invalid line (not enough columns): {line.strip()}"
                            )
                            stats["error"] += 1
                            continue

                        name, artist = parts[0], parts[1]

                        # Basic validation
                        if not name or not artist:
                            logger.warning(
                                f"Line {line_num}: Skipping invalid line (empty name or artist): {line.strip()}"
                            )
                            stats["error"] += 1
                            continue

                        logger.info(f"Validating: {name} by {artist}")

                        # Step 1: Check if song exists in Spotify
                        query = f"track:{name} artist:{artist}"
                        results = spotify.sp.search(query, type="track", limit=1)

                        if not results.get("tracks", {}).get("items", []):
                            logger.warning(
                                f"Line {line_num}: Song not found in Spotify: {name} by {artist}"
                            )
                            stats["not_found"] += 1
                            continue

                        track = results.get("tracks", {}).get("items", [])[0]
                        track_uri = track["uri"]

                        # Step 2: Check artist popularity
                        if not track.get("artists"):
                            logger.warning(f"Line {line_num}: No artist data for track: {name}")
                            stats["not_found"] += 1
                            continue
                        artist_id = track["artists"][0]["id"]
                        artist_info = spotify.sp.artist(artist_id)

                        # Get follower count
                        follower_count = (artist_info.get("followers") or {}).get("total", 0)

                        if follower_count >= 1000000:
                            logger.warning(
                                f"Line {line_num}: Artist too popular ({follower_count:,} followers): {artist}"
                            )
                            stats["popular_artist"] += 1
                            continue

                        # Song passed validation, add to database
                        song = Song(
                            id=f"{artist}|||{name}",
                            name=name,
                            artist=artist,
                            spotify_uri=track_uri,
                            first_added=datetime.now(),
                        )

                        if self.db.add_song(song):
                            logger.info(f"Added: {song.name} by {song.artist}")
                            stats["added"] += 1
                        else:
                            logger.info(f"Skipped (already exists): {song.name} by {song.artist}")
                            stats["already_exists"] += 1

                    except Exception as e:
                        logger.warning(f"Line {line_num}: Error processing line: {str(e)}")
                        stats["error"] += 1
                        continue

            # Display import statistics
            section("Import Summary")
            key_value_table(
                [
                    ["Total entries processed", stats["total"]],
                    ["Songs added", stats["added"]],
                    ["Songs already in database", stats["already_exists"]],
                    ["Songs not found in Spotify", stats["not_found"]],
                    ["Artists with >=1M followers", stats["popular_artist"]],
                    ["Errors", stats["error"]],
                ]
            )

        except Exception as e:
            logger.error(f"Error importing songs: {str(e)}")

    def update_playlist(
        self,
        playlist_name: str,
        song_count: int = 10,
        fresh_days: int = 30,
        dry_run: bool = False,
        score_strategy: str = "local",
        query: Optional[str] = None,
    ):
        """Update a playlist with new songs by deleting and recreating it

        Args:
            playlist_name: Name of the playlist to update
            song_count: Number of songs to include in the playlist
            fresh_days: Prioritize songs not listened to in this many days
            dry_run: If True, preview selection without updating Spotify
            score_strategy: Match scoring strategy (local, web, hybrid)
            query: Optional theme query for building a playlist profile
        """
        try:
            rm = self._get_rotation_manager(playlist_name)
            score_config = PlaylistScoreConfig(strategy=score_strategy, query=query)

            # Select songs
            logger.info(
                f"Selecting {song_count} songs (prioritizing songs not used in {fresh_days} days)..."
            )
            songs = rm.select_songs_for_today(
                count=song_count, fresh_days=fresh_days, score_config=score_config
            )

            if dry_run:
                section("Dry Run", "Selected Songs")
                table_data = [[i, s.name, s.artist] for i, s in enumerate(songs, 1)]
                table(["#", "Song", "Artist"], table_data)
                info(f"Total selected: {len(songs)}")
                return

            # Update playlist
            logger.info("Updating playlist...")
            if rm.update_playlist(songs):
                # Show detailed stats
                stats = rm.get_rotation_stats()
                section("Playlist Update Stats")
                key_value_table(
                    [
                        ["Total songs in database", stats.total_songs],
                        ["Songs used so far", stats.unique_songs_used],
                        ["Songs never used", stats.songs_never_used],
                        ["Total generations", stats.generations_count],
                        ["Complete rotation achieved", stats.complete_rotation_achieved],
                    ]
                )
            else:
                logger.error("Failed to update playlist")

        except Exception as e:
            logger.error(f"Error updating playlist: {str(e)}")
            logger.debug("Full error:", exc_info=True)

    def _show_detailed_stats(self, rm: RotationManager):
        """Show detailed statistics about the playlist"""
        stats = rm.get_rotation_stats()
        recent_songs = rm.get_recent_songs(days=7)

        # Basic stats
        section("Playlist Statistics")
        stats_table = [
            ["Total Songs", stats.total_songs],
            ["Songs Used", stats.unique_songs_used],
            ["Songs Never Used", stats.songs_never_used],
            ["Total generations", stats.generations_count],
            ["Complete Rotation", "Yes" if stats.complete_rotation_achieved else "No"],
            [
                "Rotation Progress",
                f"{(stats.unique_songs_used / stats.total_songs) * 100:.1f}%"
                if stats.total_songs
                else "0.0%",
            ],
            ["Current Strategy", stats.current_strategy],
        ]
        key_value_table(stats_table)

        # Recent activity
        section("Recent Activity", "Last 7 Days")
        recent_table = []
        for date, songs in recent_songs.items():
            recent_table.append(
                [
                    date,
                    len(songs),
                    ", ".join(f"{s.name} by {s.artist}"[:40] for s in songs[:3])
                    + ("..." if len(songs) > 3 else ""),
                ]
            )
        table(
            ["Date", "Songs Added", "Sample Songs"],
            recent_table,
        )

    def restore_previous_rotation(self, playlist_name: str, offset: int = -1):
        """
        Restore a playlist to a previous rotation by going 'offset' generations back.
        If out of range, inform user and do nothing.
        """
        try:
            rm = self._get_rotation_manager(playlist_name)

            # Calculate the generation index to restore
            new_gen_index = rm.history.current_generation + offset
            if new_gen_index < 0 or new_gen_index >= len(rm.history.generations):
                logger.error(
                    f"Offset {offset} is out of bounds. Valid range: 0 to {-(len(rm.history.generations))} "
                    f"(or up to {len(rm.history.generations) - 1} if you prefer positive indexes)."
                )
                return

            # Retrieve songs from that generation
            old_song_ids = rm.history.generations[new_gen_index]
            songs_to_restore = []
            for sid in old_song_ids:
                song = self.db.get_song_by_id(sid)
                if song:
                    songs_to_restore.append(song)

            if not songs_to_restore:
                logger.info(f"No songs found in generation index {new_gen_index}.")
                return

            # Update playlist with these songs
            logger.info(
                f"Restoring playlist '{playlist_name}' to generation index {new_gen_index}..."
            )
            # Don't record a new generation when reverting
            success = rm.update_playlist(songs_to_restore, record_generation=False)
            if success:
                rm.history.current_generation = new_gen_index
                rm._save_history()
                logger.info("Playlist successfully restored to the requested generation.")
            else:
                logger.error("Failed to restore playlist.")
        except Exception as e:
            logger.error(f"Error restoring previous rotation: {str(e)}")
            logger.debug("Full error:", exc_info=True)

    def list_rotations(self, playlist_name: str, generations: str = "3"):
        """List rotations for a given playlist

        Args:
            playlist_name: Name of the playlist
            generations: Number of generations to list, or 'all' for all generations
        """
        try:
            rm = self._get_rotation_manager(playlist_name)
            if not rm.history.generations:
                info(f"No rotations found for playlist '{playlist_name}'.")
                return

            # Determine how many generations to show
            gens_str = generations.lower()
            all_gens = rm.history.generations
            if gens_str == "all":
                limit = len(all_gens)
                logger.info(f"Showing all {limit} generations")
            else:
                try:
                    limit = int(gens_str)
                    if limit <= 0:
                        warning("Number of generations must be positive.")
                        return
                except ValueError:
                    warning("Invalid --generations value. Must be an integer or 'all'.")
                    return

            # Handle out-of-bounds
            if limit > len(all_gens):
                logger.info(f"Requested {limit} generations, but only {len(all_gens)} available.")
                limit = len(all_gens)

            # Get the most recent N generations
            selected_gens = all_gens[-limit:]

            section("Rotations", f"Playlist: {playlist_name}")
            # Calculate the starting index for proper numbering
            start_idx = len(all_gens) - limit + 1
            for i, gen_songs in enumerate(selected_gens, start=start_idx):
                subsection(f"Generation {i}")
                songs = []
                for song_id in gen_songs:
                    song = self.db.get_song_by_id(song_id)
                    if song:
                        songs.append(song)

                # Display songs in a tabular format
                if songs:
                    table_data = []
                    for j, song in enumerate(songs, 1):
                        table_data.append([j, song.name, song.artist])
                    table(["#", "Song", "Artist"], table_data)
                else:
                    info("   No songs found for this generation.")

            info(f"Current generation: {rm.history.current_generation + 1}")
        except Exception as e:
            logger.error(f"Error listing rotations: {str(e)}")
            logger.debug("Full error:", exc_info=True)

    def view_playlist(self, playlist_name: str):
        """View current playlist contents - only needs Spotify"""
        try:
            # Only initialize Spotify manager
            tracks = self.spotify.get_playlist_tracks(playlist_name)

            section("Current Playlist", playlist_name)

            if not tracks:
                warning("Playlist is empty.")
                return

            # Prepare table data
            table_data = []
            for i, track in enumerate(tracks, 1):
                added_date = track.get("added_at", "")
                if added_date:
                    try:
                        # Convert from ISO format to YYYY-MM-DD
                        added_date = added_date.split("T")[0]
                    except (AttributeError, IndexError):
                        added_date = "Unknown"

                table_data.append([i, track["name"], track["artist"], added_date or "Unknown"])

            table(
                ["#", "Song", "Artist", "Added Date"],
                table_data,
            )

            # Show summary
            info(f"Total tracks: {len(tracks)}")

        except Exception as e:
            logger.error(f"Error viewing playlist: {str(e)}")
            logger.debug("Full error:", exc_info=True)

    def show_profile(self, top: int = 15) -> Optional[Dict[str, Any]]:
        """Visualize the library: top artists by track count + rotation coverage.

        Built entirely on fully-populated columns (track/artist identity and
        rotation history), so it is honest on the current corpus — unlike
        mood/genre views, which depend on track enrichment that has not run yet.

        Returns the underlying data as a dict (also used for `--json`); None only
        on error.
        """
        try:
            conn = self.repos.conn
            total_tracks = conn.execute("SELECT COUNT(*) AS c FROM tracks").fetchone()["c"]
            if not total_tracks:
                info("No tracks in your library yet. Try /ingest or /search to add some.")
                return {
                    "tracks": 0,
                    "artists": 0,
                    "rotated": 0,
                    "never_rotated": 0,
                    "generations": 0,
                    "top_artists": [],
                    "coverage_growth": [],
                }

            total_artists = conn.execute("SELECT COUNT(*) AS c FROM artists").fetchone()["c"]
            rotated = conn.execute(
                "SELECT COUNT(DISTINCT track_id) AS c FROM generation_tracks"
            ).fetchone()["c"]
            generations = conn.execute("SELECT COUNT(*) AS c FROM rotation_generations").fetchone()[
                "c"
            ]
            never = total_tracks - rotated

            def _pct(part: int) -> str:
                return f"{part} ({part / total_tracks * 100:.0f}%)"

            section("Library Profile")
            key_value_table(
                [
                    ["Tracks", total_tracks],
                    ["Artists", total_artists],
                    ["Rotated at least once", _pct(rotated)],
                    ["Never rotated", _pct(never)],
                    ["Rotation generations", generations],
                ]
            )
            if never:
                info(
                    f"{never} of your {total_tracks} tracks have never been rotated "
                    f"— plenty of unused library to draw from."
                )

            # Top artists by track count (drops tracks with no artist via the join).
            artist_rows = conn.execute(
                """
                SELECT a.name AS name, COUNT(*) AS c
                FROM tracks t
                JOIN artists a ON t.artist_id = a.artist_id
                GROUP BY t.artist_id
                ORDER BY c DESC, name ASC
                LIMIT ?
                """,
                (top,),
            ).fetchall()
            if artist_rows:
                section("Top artists", f"by track count (top {len(artist_rows)})")
                bar_chart([r["name"] for r in artist_rows], [r["c"] for r in artist_rows])

            # Cumulative distinct-track coverage across generations (a discovery curve).
            gen_rows = conn.execute(
                "SELECT generation_id AS gid FROM rotation_generations "
                "ORDER BY generation_index ASC"
            ).fetchall()
            growth: list[int] = []
            if len(gen_rows) >= 2:
                seen: set[str] = set()
                for gen in gen_rows:
                    for row in conn.execute(
                        "SELECT track_id FROM generation_tracks WHERE generation_id = ?",
                        (gen["gid"],),
                    ).fetchall():
                        seen.add(row["track_id"])
                    growth.append(len(seen))
                section("Rotation coverage growth", f"{len(gen_rows)} generations")
                key_value_table(
                    [
                        ["Coverage curve", sparkline(growth)],
                        ["First → latest", f"{growth[0]} → {growth[-1]} distinct tracks"],
                    ]
                )

            return {
                "tracks": total_tracks,
                "artists": total_artists,
                "rotated": rotated,
                "never_rotated": never,
                "generations": generations,
                "top_artists": [{"name": r["name"], "tracks": r["c"]} for r in artist_rows],
                "coverage_growth": growth,
            }
        except Exception as e:
            logger.error(f"Error showing profile: {str(e)}")
            return None

    def _taste_seed(self) -> Tuple[list, str]:
        """Return (rows, source_label) of embedded tracks that define current taste.

        Prefers real listening (listen_events), falls back to rotation membership,
        then the whole library. Uses JOINs (not Python IN-lists) so it stays correct
        even when the seed is the full corpus (past SQLite's bound-variable limit).
        """
        conn = self.repos.conn
        if conn.execute("SELECT 1 FROM listen_events LIMIT 1").fetchone():
            rows = conn.execute(
                "SELECT te.track_id AS track_id, te.embedding_blob AS embedding_blob "
                "FROM track_embeddings te JOIN listen_events le ON le.track_id = te.track_id "
                "GROUP BY te.track_id ORDER BY MAX(le.played_at) DESC LIMIT 200"
            ).fetchall()
            if rows:
                return rows, "recent plays"
        if conn.execute("SELECT 1 FROM generation_tracks LIMIT 1").fetchone():
            rows = conn.execute(
                "SELECT DISTINCT te.track_id AS track_id, te.embedding_blob AS embedding_blob "
                "FROM track_embeddings te JOIN generation_tracks gt ON gt.track_id = te.track_id"
            ).fetchall()
            if rows:
                return rows, "your rotation"
        rows = conn.execute("SELECT track_id, embedding_blob FROM track_embeddings").fetchall()
        return rows, "your library"

    def _taste_rows(self, items: list) -> list:
        """Resolve a list of (track_id, vec) pairs to [{track_id, name, artist}]."""
        rows = []
        for track_id, _vec in items:
            track = self.repos.tracks.get(track_id) or {}
            artist = self.repos.artists.get(track.get("artist_id") or "") or {}
            rows.append(
                {
                    "track_id": track_id,
                    "name": track.get("name") or track_id,
                    "artist": artist.get("name") or "?",
                }
            )
        return rows

    def _taste_display_rows(self, items: list, start: int = 1) -> list:
        """Build [#, Track, Artist] table rows for a list of (track_id, vec) pairs."""
        return [
            [i, row["name"], row["artist"]] for i, row in enumerate(self._taste_rows(items), start)
        ]

    def _sonic_vectors_for(self, track_ids: list) -> Dict[str, list]:
        """Load stored AcousticBrainz sonic vectors for the given track_ids (only
        those that have been /sonic-backfilled appear in the result)."""
        out: Dict[str, list] = {}
        for track_id in track_ids:
            row = self.repos.sonic.get(track_id)
            if row and row.get("sonic_blob") is not None:
                out[track_id] = decode_vector(row["sonic_blob"])
        return out

    def show_taste(self, top: int = 8) -> Optional[Dict[str, Any]]:
        """Render a 'current taste' card: the tracks most/least representative of the
        centroid of your recent-listening (or rotation) embeddings.

        HONEST CAVEAT: until /enrich runs, embeddings are derived from track titles +
        artists (text), not acoustic features — so this is a SEMANTIC taste, not a
        sonic one. Rows are ranked by closeness to the centroid; we deliberately show
        RANKING, not raw cosine %, since normalized text-embedding cosines sit in a
        compressed band where absolute percentages mislead.

        Returns the underlying data as a dict (also used for `--json`); None only
        on error or too-sparse data.
        """
        try:
            conn = self.repos.conn
            rows, source = self._taste_seed()
            seed = [(r["track_id"], decode_vector(r["embedding_blob"])) for r in rows]
            if len(seed) < 3:
                info(
                    f"Not enough embedded tracks ({len(seed)}) to profile your taste yet — "
                    "try /ingest or /search to add more."
                )
                return None

            centroid = taste_centroid(vec for _, vec in seed)
            cnorm = vector_norm(centroid) or 1.0

            def cos_to_centroid(vec: list) -> float:
                denom = vector_norm(vec) * cnorm
                return sum(a * b for a, b in zip(vec, centroid)) / denom if denom else 0.0

            # Sonic channel: where tracks have AcousticBrainz vectors, blend a sonic
            # similarity into the ranking — coverage-aware, so tracks WITHOUT sonic data
            # are scored on text alone and never penalized for missing it.
            sonic_vecs = self._sonic_vectors_for([tid for tid, _ in seed])
            sonic_centroid = taste_centroid(sonic_vecs.values()) if len(sonic_vecs) >= 3 else []
            scnorm = vector_norm(sonic_centroid) or 1.0

            def _sonic_sim(track_id: str) -> Optional[float]:
                vec = sonic_vecs.get(track_id)
                if not (sonic_centroid and vec):
                    return None
                denom = vector_norm(vec) * scnorm
                return sum(a * b for a, b in zip(vec, sonic_centroid)) / denom if denom else 0.0

            def _minmax_fn(values: list) -> Callable[[float], float]:
                vals = list(values)
                if not vals:
                    return lambda _v: 0.0
                lo, hi = min(vals), max(vals)
                span = hi - lo
                if span == 0:
                    return lambda _v: 1.0
                return lambda v: (v - lo) / span

            text_sims = {tid: cos_to_centroid(vec) for tid, vec in seed}
            sonic_sims = {tid: _sonic_sim(tid) for tid, _ in seed}
            text_norm = _minmax_fn(list(text_sims.values()))
            present = [v for v in sonic_sims.values() if v is not None]
            sonic_norm = _minmax_fn(present) if present else None
            sonic_weight = 0.5

            def _representativeness(track_id: str) -> float:
                base = text_norm(text_sims[track_id])
                value = sonic_sims[track_id]
                if sonic_norm is not None and value is not None:
                    return sonic_weight * sonic_norm(value) + (1 - sonic_weight) * base
                return base

            ranked = sorted(seed, key=lambda tv: _representativeness(tv[0]), reverse=True)
            # Distinct artists are free: track_id is "artist|||name".
            n_artists = len({track_id.split("|||")[0] for track_id, _ in seed})

            enriched = bool(conn.execute("SELECT 1 FROM track_context LIMIT 1").fetchone())
            signal = (
                "enriched (mood/genre/era + titles)"
                if enriched
                else "text-based (titles + artists)"
            )

            section("Your Taste", f"{source} · {signal}")
            key_value_table(
                [
                    ["Built from", f"{len(seed)} tracks"],
                    ["Distinct artists", n_artists],
                    ["Signal", signal],
                ]
            )

            most = ranked[:top]
            subsection("Most representative")
            table(["#", "Track", "Artist"], self._taste_display_rows(most))

            widest: list = []
            if len(ranked) > top:
                widest = list(reversed(ranked[top:]))[:top]  # lowest-cosine tracks first
                subsection("Widest-ranging")
                table(["#", "Track", "Artist"], self._taste_display_rows(widest))

            # "Your sound": the average acoustic profile, where AcousticBrainz data
            # exists (plain mean, so the 0–1 feature values stay interpretable).
            sonic_profile: Optional[Dict[str, float]] = None
            if len(sonic_vecs) >= 3:
                sonic_profile = describe_sonic(mean_vector(sonic_vecs.values()))
                feel = {k: v for k, v in sonic_profile.items() if k != "bpm"}
                top_feel = sorted(feel.items(), key=lambda kv: kv[1], reverse=True)[:6]
                subsection("Your sound")
                bar_chart(
                    [k.replace("mood_", "").replace("_", " ") for k, _ in top_feel],
                    [v for _, v in top_feel],
                    value_fmt=lambda v: f"{v:.2f}",
                )
                info(
                    f"~{sonic_profile.get('bpm', '?')} BPM avg · acoustic features on "
                    f"{len(sonic_vecs)}/{len(seed)} tracks (AcousticBrainz). "
                    "The ranking above blends this with the text taste where present."
                )

            hint = (
                "Ranked by closeness to your taste centroid."
                if enriched
                else "Ranked by closeness to your taste centroid. This is a TEXT/semantic "
                "profile (titles + artists) — run /enrich to make it reflect mood, genre, and sound."
            )
            info(hint)

            return {
                "source": source,
                "signal": signal,
                "enriched": enriched,
                "built_from": len(seed),
                "distinct_artists": n_artists,
                "sonic_coverage": len(sonic_vecs),
                "sonic_profile": sonic_profile,
                "most_representative": self._taste_rows(most),
                "widest_ranging": self._taste_rows(widest),
            }
        except Exception as e:
            logger.error(f"Error showing taste: {str(e)}")
            return None

    def taste_rank_last_search(self, taste_weight: float = 0.5) -> Tuple[List[Dict[str, Any]], str]:
        """Re-rank the last search's results by a blend of search-relevance and
        closeness to the user's taste centroid.

        Both signals are min-max normalized *within this result set* before
        blending (`blended = w*taste + (1-w)*relevance`) — raw cosine values sit
        in a compressed band and aren't comparable to relevance scores. When
        there's no usable taste signal (too few embedded seed tracks, or no result
        carries an embedding), the blend collapses to pure relevance and the
        returned signal string says so. Returns (ranked_rows, signal).
        """
        results = self.last_search_results or []
        if not results:
            return [], "no results"

        seed_rows, source = self._taste_seed()
        seed = [(r["track_id"], decode_vector(r["embedding_blob"])) for r in seed_rows]
        enriched = bool(self.repos.conn.execute("SELECT 1 FROM track_context LIMIT 1").fetchone())
        base = "enriched (mood/genre/era)" if enriched else "text-based (titles + artists)"
        have_taste = len(seed) >= 3
        centroid = taste_centroid(vec for _, vec in seed) if have_taste else []
        cnorm = (vector_norm(centroid) or 1.0) if have_taste else 1.0

        rows: List[Dict[str, Any]] = []
        for item in results:
            track_id = item.get("track_id")
            taste_sim: Optional[float] = None
            if have_taste and track_id:
                emb = self.repos.embeddings.get(track_id)
                if emb and emb.get("embedding_blob") is not None:
                    vec = decode_vector(emb["embedding_blob"])
                    denom = vector_norm(vec) * cnorm
                    taste_sim = sum(a * b for a, b in zip(vec, centroid)) / denom if denom else 0.0
            rows.append(
                {
                    "song": item.get("song"),
                    "artist": item.get("artist"),
                    "year": item.get("year"),
                    "track_id": track_id,
                    "relevance": float(item.get("score") or 0.0),
                    "taste_sim": taste_sim,
                }
            )

        def _minmax(values: List[float]) -> Callable[[float], float]:
            lo, hi = min(values), max(values)
            span = hi - lo
            if span == 0:
                return lambda _v: 1.0  # all tied on this axis -> let the other axis decide
            return lambda v: (v - lo) / span

        rel_norm = _minmax([r["relevance"] for r in rows])
        taste_present = have_taste and any(r["taste_sim"] is not None for r in rows)
        if taste_present:
            taste_norm = _minmax([r["taste_sim"] for r in rows if r["taste_sim"] is not None])
            weight = max(0.0, min(1.0, taste_weight))
            signal = f"{source} · {base}"
        else:
            weight = 0.0
            signal = f"no taste signal yet — ranking on relevance only ({base})"

        for row in rows:
            row["rel_norm"] = rel_norm(row["relevance"])
            row["taste_norm"] = (
                taste_norm(row["taste_sim"])
                if (taste_present and row["taste_sim"] is not None)
                else 0.0
            )
            row["blended"] = weight * row["taste_norm"] + (1 - weight) * row["rel_norm"]

        rows.sort(key=lambda r: r["blended"], reverse=True)
        return rows, signal

    def show_stats(self, playlist_name: Optional[str] = None):
        """Show database and playlist statistics"""
        try:
            # Database stats
            db_stats = self.db.get_stats()
            section("Database Stats")
            key_value_table(
                [
                    ["Total songs", db_stats["total_songs"]],
                    ["Embedding dimensions", db_stats["embedding_dimensions"]],
                    ["Storage size (MB)", f"{db_stats['storage_size_mb']:.2f}"],
                ]
            )

            # Playlist stats if specified
            if playlist_name:
                rm = self._get_rotation_manager(playlist_name)
                stats = rm.get_rotation_stats()

                section("Playlist Stats", playlist_name)
                key_value_table(
                    [
                        ["Total songs", stats.total_songs],
                        ["Songs used so far", stats.unique_songs_used],
                        ["Songs never used", stats.songs_never_used],
                        ["Total generations", stats.generations_count],
                        ["Complete rotation achieved", stats.complete_rotation_achieved],
                    ]
                )

                # Show recent generations
                recent_gens = rm.get_recent_generations(count=5)
                if recent_gens:
                    section("Recent Generations")
                    for i, gen_songs in enumerate(recent_gens, 1):
                        gen_index = stats.generations_count - len(recent_gens) + i
                        subsection(f"Generation {gen_index}")
                        table_data = []
                        for j, song in enumerate(gen_songs, 1):
                            table_data.append([j, song.name, song.artist])
                        table(["#", "Song", "Artist"], table_data)
                else:
                    info("No generation history found.")

        except Exception as e:
            logger.error(f"Error showing stats: {str(e)}")

    def export_stats(
        self, playlist_name: Optional[str], export_format: str, output_file: Optional[str]
    ):
        """Export database and playlist stats to a file."""
        db_stats = self.db.get_stats()
        export_payload = {
            "database": db_stats,
            "playlist": None,
            "generated_at": datetime.now().isoformat(),
        }

        if playlist_name:
            rm = self._get_rotation_manager(playlist_name)
            stats = rm.get_rotation_stats()
            export_payload["playlist"] = {
                "name": playlist_name,
                "total_songs": stats.total_songs,
                "unique_songs_used": stats.unique_songs_used,
                "songs_never_used": stats.songs_never_used,
                "generations_count": stats.generations_count,
                "complete_rotation_achieved": stats.complete_rotation_achieved,
                "current_strategy": stats.current_strategy,
            }

        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = "json" if export_format == "json" else "csv"
            output_file = f"stats_export_{timestamp}.{suffix}"

        if export_format == "json":
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(export_payload, f, indent=2)
            info(f"Exported stats to {output_file}")
            return

        # CSV export: flattened key/value pairs
        rows = []
        for key, value in db_stats.items():
            rows.append(["database", key, value])

        if export_payload["playlist"]:
            for key, value in export_payload["playlist"].items():
                rows.append(["playlist", key, value])

        with open(output_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["section", "key", "value"])
            writer.writerows(rows)
        info(f"Exported stats to {output_file}")

    def sync_playlist(self, playlist_name: str):
        """Sync a playlist with all songs in the database by adding new songs and removing songs no longer in the database"""
        try:
            logger.info(f"Starting database sync with playlist '{playlist_name}'...")

            # Get all songs from database
            all_songs = self.db.get_all_songs()
            if not all_songs:
                logger.error("No songs found in database")
                return

            logger.info(f"Found {len(all_songs)} songs in database")

            # Get existing tracks in the playlist
            existing_tracks = self.spotify.get_playlist_tracks(playlist_name)
            if existing_tracks is None:
                logger.error(f"Failed to retrieve tracks from playlist '{playlist_name}'")
                return

            # Create a set of existing URIs for quick lookup
            existing_uris = set()
            for track in existing_tracks:
                if "uri" in track:
                    existing_uris.add(track["uri"])

            logger.info(f"Found {len(existing_uris)} existing tracks in playlist")

            # Create a set of database URIs for quick lookup
            database_uris = set()
            songs_to_add = []

            for song in all_songs:
                # If song doesn't have a URI yet, try to find it
                if not song.spotify_uri:
                    song.spotify_uri = self.spotify.search_song(song)

                if song.spotify_uri:
                    database_uris.add(song.spotify_uri)

                    # Only add songs with URIs that aren't already in the playlist
                    if song.spotify_uri not in existing_uris:
                        songs_to_add.append(song)

            # Find tracks to remove (in playlist but not in database)
            uris_to_remove = existing_uris - database_uris

            logger.info(f"Found {len(songs_to_add)} new songs to add to playlist")
            logger.info(f"Found {len(uris_to_remove)} songs to remove from playlist")

            # Add new songs if needed
            if songs_to_add:
                add_success = self.spotify.append_to_playlist(playlist_name, songs_to_add)
                if add_success:
                    # Persist any URI changes discovered during Spotify search
                    self.db._save_state()
                    logger.info(
                        f"Successfully added {len(songs_to_add)} new songs to playlist '{playlist_name}'"
                    )
                else:
                    logger.error("Failed to add new songs to playlist")
            else:
                logger.info("No new songs to add")

            # Remove songs if needed
            if uris_to_remove:
                remove_success = self.spotify.remove_from_playlist(
                    playlist_name, list(uris_to_remove)
                )
                if remove_success:
                    logger.info(
                        f"Successfully removed {len(uris_to_remove)} songs from playlist '{playlist_name}'"
                    )
                else:
                    logger.error("Failed to remove songs from playlist")
            else:
                logger.info("No songs to remove")

            if not songs_to_add and not uris_to_remove:
                logger.info("Playlist is already in sync with the database")

        except Exception as e:
            logger.error(f"Error syncing playlist: {str(e)}")
            logger.debug("Full error:", exc_info=True)

    def extract_playlist(self, playlist_name: str, output_file: str = None):
        """Extract playlist contents to a CSV file"""
        try:
            # Get tracks from playlist
            tracks = self.spotify.get_playlist_tracks(playlist_name)

            if not tracks:
                logger.error(f"No tracks found in playlist '{playlist_name}'")
                return False

            # Generate output filename if not provided
            if output_file is None:
                output_file = f"{playlist_name}_songs.csv"

            # Ensure file extension is .csv
            if not output_file.endswith(".csv"):
                output_file += ".csv"

            # Write to file
            logger.info(f"Writing {len(tracks)} tracks to {output_file}")
            with open(output_file, "w", encoding="utf-8") as f:
                for track in tracks:
                    f.write(f"{track['name']},{track['artist']}\n")

            logger.info(f"Successfully exported playlist to {output_file}")
            return True

        except Exception as e:
            logger.error(f"Error extracting playlist: {str(e)}")
            return False

    def backup_data(self, backup_name: Optional[str] = None):
        """
        Create a backup of the entire data/ folder in a new backups/ directory
        at the same level as src/.
        """
        project_root = Path(__file__).parent.parent
        data_dir = project_root / "data"
        backups_dir = project_root / "backups"
        backups_dir.mkdir(exist_ok=True)

        # Generate a backup folder name
        if not backup_name:
            # Use YYYYMMDD_HHMMSS format
            backup_name = datetime.now().strftime("%Y%m%d_%H%M%S")

        backup_folder = backups_dir / backup_name

        if backup_folder.exists():
            logger.warning(f"Backup folder '{backup_folder.name}' already exists. Aborting.")
            return

        logger.info(f"Creating backup '{backup_folder.name}' from data folder...")
        try:
            shutil.copytree(str(data_dir), str(backup_folder))
            logger.info(f"Backup '{backup_folder.name}' created successfully.")
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            if backup_folder.exists():
                shutil.rmtree(str(backup_folder), ignore_errors=True)
                logger.info("Cleaned up partial backup.")

    def restore_data(self, backup_name: str) -> bool:
        """
        Restore data/ from the chosen backup in backups/.

        Atomic: the restored copy is built in a staging directory BEFORE the
        live data/ is touched, then swapped in via rename. On any failure the
        live data is left intact (or rolled back), and on success the
        moved-aside old data dir is removed so it does not leak.
        """
        project_root = Path(__file__).parent.parent
        data_dir = project_root / "data"
        backups_dir = project_root / "backups"
        backup_folder = backups_dir / backup_name

        if not backup_folder.exists():
            logger.error(f"No such backup folder: '{backup_folder.name}'")
            return False

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        staging = project_root / f".data_restore_{ts}"

        # Build the new copy first; live data/ is untouched if this fails.
        logger.info(f"Restoring backup '{backup_folder.name}' to data/ ...")
        try:
            shutil.copytree(str(backup_folder), str(staging))
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            shutil.rmtree(str(staging), ignore_errors=True)
            return False

        old = None
        try:
            if data_dir.exists():
                logger.info("Renaming existing data folder...")
                old = project_root / f"data_old_{ts}"
                data_dir.rename(old)  # move live aside
                logger.info(f"Renamed existing data/ to {old.name}")
            staging.rename(data_dir)  # atomic swap (same filesystem)
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            if old is not None and old.exists() and not data_dir.exists():
                old.rename(data_dir)  # rollback
                logger.info("Rolled back to previous data directory.")
            shutil.rmtree(str(staging), ignore_errors=True)
            return False

        if old is not None:
            # Success: remove the moved-aside copy (fixes the data_old_<ts> leak).
            shutil.rmtree(str(old), ignore_errors=True)

        logger.info(f"Data successfully restored from '{backup_folder.name}'.")
        return True

    def list_backups(self):
        """List all available backups with their sizes and dates"""
        project_root = Path(__file__).parent.parent
        backups_dir = project_root / "backups"

        if not backups_dir.exists():
            info("No backups directory found.")
            return

        # Get all backup folders
        backup_folders = sorted(
            backups_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        )

        if not backup_folders:
            info("No backups found.")
            return

        section("Available Backups")

        # Prepare table data
        table_data = []
        for backup in backup_folders:
            if backup.is_dir():
                # Calculate folder size
                total_size = sum(f.stat().st_size for f in backup.rglob("*") if f.is_file())
                size_mb = total_size / (1024 * 1024)

                # Get modification time
                mod_time = datetime.fromtimestamp(backup.stat().st_mtime)
                date_str = mod_time.strftime("%Y-%m-%d %H:%M:%S")

                table_data.append([backup.name, f"{size_mb:.2f} MB", date_str])

        if table_data:
            table(["Backup Name", "Size", "Created"], table_data)
            info(f"Total backups: {len(table_data)}")
            info("Use 'restore <backup_name>' to restore a backup.")
        else:
            info("No backup folders found.")

    def clean_database(self, dry_run: bool = False):
        """Clean database by removing songs that no longer exist in Spotify
        or whose artists have 1 million or more monthly listeners

        Args:
            dry_run: If True, only show what would be removed without actually removing
        """
        try:
            logger.info("Starting database cleaning process...")

            # Get all songs from database
            all_songs = self.db.get_all_songs()
            if not all_songs:
                logger.info("No songs found in database")
                return

            logger.info(f"Checking {len(all_songs)} songs in database")

            # Initialize Spotify for validation
            spotify = self.spotify

            # Track statistics
            stats = {
                "total": len(all_songs),
                "checked": 0,
                "not_found": 0,
                "popular_artist": 0,
                "kept": 0,
            }

            # Songs to remove
            songs_to_remove = []

            # Check each song
            from tqdm import tqdm

            for song in tqdm(
                all_songs,
                desc="Checking songs",
                disable=os.getenv("TUNR_INTERACTIVE") == "1",
            ):
                stats["checked"] += 1

                # Skip songs that already have a Spotify URI (optimization)
                if song.spotify_uri:
                    # Verify the URI still works
                    try:
                        track_info = spotify.get_track_info(song.spotify_uri)
                        if track_info:
                            # Check artist popularity
                            track = spotify.sp.track(song.spotify_uri)
                            if not track.get("artists"):
                                logger.warning(f"No artist data for track: {song.name}")
                                # Continue to search fallback
                            else:
                                artist_id = track["artists"][0]["id"]
                                artist_info = spotify.sp.artist(artist_id)
                                follower_count = (artist_info.get("followers") or {}).get(
                                    "total", 0
                                )

                                if follower_count >= 1000000:
                                    logger.warning(
                                        f"Artist too popular ({follower_count:,} followers): {song.artist}"
                                    )
                                    songs_to_remove.append(song)
                                    stats["popular_artist"] += 1
                                    continue

                                stats["kept"] += 1
                                continue
                    except Exception as e:
                        # URI no longer valid, continue with search
                        logger.debug(f"URI validation failed for {song.name}: {e}")

                # Search for the song on Spotify
                query = f"track:{song.name} artist:{song.artist}"
                results = spotify.sp.search(query, type="track", limit=1)

                if not results.get("tracks", {}).get("items", []):
                    # Song not found in Spotify
                    logger.warning(f"Song not found in Spotify: {song.name} by {song.artist}")
                    songs_to_remove.append(song)
                    stats["not_found"] += 1
                else:
                    # Song found, update URI if needed
                    track = results.get("tracks", {}).get("items", [])[0]
                    if not song.spotify_uri:
                        song.spotify_uri = track["uri"]
                        self.db._save_state()  # Save the updated URI

                    # Check artist popularity
                    if not track.get("artists"):
                        logger.warning(f"No artist data for track: {song.name}")
                        stats["kept"] += 1
                    else:
                        artist_id = track["artists"][0]["id"]
                        artist_info = spotify.sp.artist(artist_id)
                        follower_count = (artist_info.get("followers") or {}).get("total", 0)

                        if follower_count >= 1000000:
                            logger.warning(
                                f"Artist too popular ({follower_count:,} followers): {song.artist}"
                            )
                            songs_to_remove.append(song)
                            stats["popular_artist"] += 1
                        else:
                            stats["kept"] += 1

            # Remove songs if not in dry run mode
            if songs_to_remove:
                if dry_run:
                    logger.info(f"DRY RUN: Would remove {len(songs_to_remove)} songs")
                    for song in songs_to_remove:
                        logger.info(f"  - {song.name} by {song.artist}")
                else:
                    logger.info(f"Removing {len(songs_to_remove)} songs from database")
                    for song in songs_to_remove:
                        logger.info(f"Removing: {song.name} by {song.artist}")
                        self.db.remove_song(song.id)

            # Display cleaning statistics
            section("Database Cleaning Results")
            key_value_table(
                [
                    ["Total songs checked", stats["checked"]],
                    ["Songs kept", stats["kept"]],
                    ["Songs not found in Spotify", stats["not_found"]],
                    ["Songs with popular artists (>=1M followers)", stats["popular_artist"]],
                ]
            )
            if dry_run and songs_to_remove:
                info("DRY RUN: No songs were actually removed.")
            elif songs_to_remove:
                info(f"Songs removed: {len(songs_to_remove)}")
            else:
                info("No songs needed to be removed.")

        except Exception as e:
            logger.error(f"Error cleaning database: {str(e)}")
            logger.debug("Full error:", exc_info=True)

    def search_songs(
        self,
        query: Union[List[str], str],
        expanded: bool = False,
    ):
        """Deep web search for new songs based on criteria (next-gen pipeline)."""
        clear_preview()
        query_text = " ".join(query) if isinstance(query, list) else str(query)
        section("Deep Search", query_text)

        def _progress(stage: str) -> None:
            info(f"Stage: {stage}")

        interactive = os.getenv("TUNR_INTERACTIVE") == "1"
        status_label = "fresh"
        live_mode = os.getenv("SEARCH_LIVE_MODE", "full").strip().lower()
        if live_mode not in {"full", "compact"}:
            live_mode = "full"
        preview_rows: List[List[object]] = []
        preview_limit = max(0, env_int("SEARCH_PREVIEW_LIMIT", 20))
        preview_stride = max(1, env_int("SEARCH_PREVIEW_STRIDE", 5))
        live_page_size = max(1, env_int("SEARCH_LIVE_PAGE_SIZE", 50))
        live_page = max(1, env_int("SEARCH_LIVE_PAGE", 1))
        stream_full = env_flag("SEARCH_STREAM_FULL", interactive)
        final_table_mode = os.getenv("SEARCH_FINAL_TABLE_MODE", "").strip().lower()
        if not final_table_mode:
            final_table_mode = "none" if (interactive and stream_full) else "full"
        if final_table_mode not in {"full", "compact", "none"}:
            final_table_mode = "full"

        if live_mode == "compact":
            live_headers = ["#", "Song", "Artist", "Score", "Strict", "Sources"]

            def _live_row(item: SearchResult, rank: int) -> List[object]:
                return [
                    rank,
                    item.song,
                    item.artist,
                    f"{item.score:.3f}",
                    f"{item.strict_ratio:.2f}",
                    len(item.sources or []),
                ]

        else:
            live_headers = [
                "#",
                "Song",
                "Artist",
                "Year",
                "Score",
                "Strict",
                "Status",
                "Providers",
                "Sources",
            ]

            def _live_row(item: SearchResult, rank: int) -> List[object]:
                return [
                    rank,
                    item.song,
                    item.artist,
                    item.year or "-",
                    f"{item.score:.3f}",
                    f"{item.strict_ratio:.2f}",
                    status_label,
                    len(item.providers or []),
                    len(item.sources or []),
                ]

        def _page_slice(rows: List[List[object]]) -> List[List[object]]:
            start = (live_page - 1) * live_page_size
            end = start + live_page_size
            if len(rows) <= start:
                return []
            return rows[start:end]

        def _on_result(item: SearchResult, rank: int, total: int) -> None:
            if interactive and (stream_full or preview_limit > 0):
                effective_limit = total if stream_full else preview_limit
                if rank <= effective_limit:
                    preview_rows.append(_live_row(item, rank))
                    if rank % preview_stride == 0 or rank == effective_limit or rank == total:
                        paged_rows = _page_slice(preview_rows)
                        if paged_rows:
                            shown_start = (live_page - 1) * live_page_size + 1
                            shown_end = shown_start + len(paged_rows) - 1
                            preview_table(
                                live_headers,
                                paged_rows,
                                title=(
                                    f"Live Results • {status_label} "
                                    f"(rows {shown_start}-{shown_end} of {min(rank, effective_limit)})"
                                ),
                            )
                if rank % 25 == 0:
                    info(f"Scored {rank}/{total} candidates")

        try:
            results, run_id = self.search_pipeline.run(
                query=query_text,
                expanded=expanded,
                progress=_progress,
                on_result=_on_result,
            )
        except Exception as exc:
            warning(str(exc))
            clear_preview()
            self._reset_search_state()
            return

        if not results:
            info("No results returned.")
            clear_preview()
            self._reset_search_state()
            return

        self.last_search_cached = getattr(self.search_pipeline, "last_cached", False)
        status_label = "cached" if self.last_search_cached else "fresh"
        rows = []
        for idx, item in enumerate(results, 1):
            rows.append(
                [
                    idx,
                    item.song,
                    item.artist,
                    item.year or "-",
                    f"{item.score:.3f}",
                    f"{item.strict_ratio:.2f}",
                    status_label,
                    len(item.providers or []),
                    len(item.sources or []),
                ]
            )

        headers = [
            "#",
            "Song",
            "Artist",
            "Year",
            "Score",
            "Strict",
            "Status",
            "Providers",
            "Sources",
        ]
        if not (interactive and stream_full):
            if final_table_mode == "compact":
                table(
                    ["#", "Song", "Artist", "Score", "Strict", "Status"],
                    [[row[0], row[1], row[2], row[4], row[5], row[6]] for row in rows],
                )
            elif final_table_mode != "none":
                table(headers, rows)
            clear_preview()
        else:
            live_rows = [_live_row(item, idx) for idx, item in enumerate(results, 1)]
            paged_rows = _page_slice(live_rows)
            if paged_rows:
                shown_start = (live_page - 1) * live_page_size + 1
                shown_end = shown_start + len(paged_rows) - 1
                preview_table(
                    live_headers,
                    paged_rows,
                    title=f"Live Results • {status_label} (rows {shown_start}-{shown_end} of {len(results)})",
                )
            if final_table_mode == "compact":
                table(
                    ["#", "Song", "Artist", "Score", "Strict", "Status"],
                    [[row[0], row[1], row[2], row[4], row[5], row[6]] for row in rows],
                )
            elif final_table_mode == "full":
                table(headers, rows)
            info(f"Live results complete: {len(results)} candidates.")

        self.last_search_results = [
            {
                "song": item.song,
                "artist": item.artist,
                "year": item.year,
                "score": item.score,
                "strict_ratio": item.strict_ratio,
                "providers": item.providers,
                "sources": item.sources,
                "track_id": item.track_id,
            }
            for item in results
        ]
        self.last_search_query = query_text
        self.last_search_summary = None
        self.last_search_metrics = []
        self.last_search_constraints = {}
        self.last_search_expanded = expanded
        self.last_search_policy = {"path": "nextgen", "expanded": expanded}
        self.last_search_run_id = run_id
        self.last_search_track_ids = [item.track_id for item in results]
        # A fresh search re-arms the interactive follow-up; the handler flips
        # this back on if --to/--save already dealt with the results.
        self.last_search_handled = False

    def ingest_tracks(
        self, source: str, name: Optional[str] = None, time_range: str = "medium_term"
    ):
        """Ingest tracks from Spotify into the SQLite cache."""
        now = datetime.utcnow().isoformat() + "Z"
        ingested = 0

        def upsert_track(track: dict, artist: dict) -> None:
            nonlocal ingested
            track_id = self._upsert_spotify_track(track, artist, now)
            if track_id:
                ingested += 1

        try:
            sp = self.spotify.sp
            if source == "liked":
                section("Ingest", "Liked Tracks")
                offset = 0
                while True:
                    batch = sp.current_user_saved_tracks(limit=50, offset=offset)
                    items = batch.get("items") or []
                    if not items:
                        break
                    for item in items:
                        track = item.get("track") or {}
                        artists = track.get("artists") or []
                        if artists:
                            upsert_track(track, artists[0])
                    offset += len(items)
                    if len(items) < 50:
                        break
            elif source == "playlist":
                if not name:
                    warning("Playlist name required for ingest playlist.")
                    return
                section("Ingest", f"Playlist: {name}")
                tracks = self.spotify.get_playlist_tracks(name)
                for entry in tracks:
                    track = {
                        "name": entry.get("name"),
                        "uri": entry.get("uri"),
                        "album": {},
                        "external_urls": {"spotify": entry.get("uri")},
                    }
                    artist = {"name": entry.get("artist")}
                    upsert_track(track, artist)
            elif source == "top":
                section("Ingest", f"Top Tracks ({time_range})")
                offset = 0
                while True:
                    batch = sp.current_user_top_tracks(
                        limit=50, offset=offset, time_range=time_range
                    )
                    items = batch.get("items") or []
                    if not items:
                        break
                    for track in items:
                        artists = track.get("artists") or []
                        if artists:
                            upsert_track(track, artists[0])
                    offset += len(items)
                    if len(items) < 50:
                        break
            elif source == "recent":
                section("Ingest", "Recently Played")
                batch = sp.current_user_recently_played(limit=50)
                items = batch.get("items") or []
                for item in items:
                    track = item.get("track") or {}
                    artists = track.get("artists") or []
                    if artists:
                        upsert_track(track, artists[0])
            else:
                warning(f"Unknown ingest source: {source}")
                return
        except Exception as exc:
            hint = scope_error_hint(exc)
            warning(hint if hint else f"Ingest failed: {exc}")
            return

        info(f"Ingested {ingested} tracks.")

    def _upsert_spotify_track(self, track: dict, artist: dict, now: str) -> Optional[str]:
        track_name = track.get("name") or ""
        artist_name = artist.get("name") or ""
        if not track_name or not artist_name:
            return None
        track_id = f"{artist_name.lower()}|||{track_name.lower()}"
        artist_id = artist_name.lower()
        self.repos.artists.upsert(
            artist_id=artist_id,
            name=artist_name,
            genres_json=json.dumps(artist.get("genres") or []),
            popularity=artist.get("popularity"),
            updated_at=now,
        )
        self.repos.tracks.upsert(
            {
                "track_id": track_id,
                "spotify_id": track.get("uri") or track.get("id"),
                "name": track_name,
                "artist_id": artist_id,
                "album_name": (track.get("album") or {}).get("name"),
                "release_date": (track.get("album") or {}).get("release_date"),
                "duration_ms": track.get("duration_ms"),
                "explicit": 1 if track.get("explicit") else 0,
                "popularity": track.get("popularity"),
                "spotify_url": (track.get("external_urls") or {}).get("spotify"),
                "status": "candidate",
                "last_decision": None,
                "decision_reason": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        return track_id

    def sync_listen_history(self, limit: int = 50) -> None:
        """Sync recently played tracks into the listen ledger."""
        now = datetime.utcnow().isoformat() + "Z"
        section("Listen Ledger", "Recently Played")
        try:
            sp = self.spotify.sp
            payload = sp.current_user_recently_played(limit=limit)
            items = payload.get("items") or []
        except Exception as exc:
            hint = scope_error_hint(exc)
            warning(hint if hint else f"Listen sync failed: {exc}")
            return

        added = 0
        for item in items:
            track = item.get("track") or {}
            artists = track.get("artists") or []
            if not artists:
                continue
            track_id = self._upsert_spotify_track(track, artists[0], now)
            if not track_id:
                continue
            played_at = item.get("played_at") or now
            spotify_id = track.get("uri") or track.get("id")
            event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{spotify_id}|{played_at}").hex
            self.repos.listen_events.upsert(
                {
                    "event_id": event_id,
                    "track_id": track_id,
                    "spotify_id": spotify_id,
                    "played_at": played_at,
                    "source": "recently_played",
                    "created_at": now,
                }
            )
            added += 1

        info(f"Recorded {added} listen events.")

    def rotate_playlist_played(self, playlist_name: str, max_replace: Optional[int] = None) -> None:
        """Rotate a playlist by removing tracks played since they were added."""
        section("Rotate (Played)", playlist_name)
        try:
            tracks = self.spotify.get_playlist_tracks(playlist_name)
        except Exception as exc:
            warning(f"Failed to load playlist: {exc}")
            return

        if not tracks:
            warning("Playlist is empty.")
            return

        def parse_ts(value: Optional[str]) -> Optional[datetime]:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None

        track_ids = []
        added_map: Dict[str, datetime] = {}
        for entry in tracks:
            uri = entry.get("uri")
            name = entry.get("name") or ""
            artist = entry.get("artist") or ""
            track_id = None
            if uri:
                record = self.repos.tracks.get_by_spotify_id(uri)
                if record:
                    track_id = record.get("track_id")
            if not track_id and name and artist:
                track_id = f"{artist.lower()}|||{name.lower()}"
            if not track_id:
                continue
            track_ids.append(track_id)
            added_at = parse_ts(entry.get("added_at"))
            if added_at:
                added_map[track_id] = added_at

        if not track_ids:
            warning("No recognizable tracks found in playlist.")
            return

        latest_map = self.repos.listen_events.list_by_track
        played_ids: List[Tuple[str, Optional[datetime], Optional[datetime]]] = []
        unplayed_ids = []

        for track_id in track_ids:
            added_at = added_map.get(track_id)
            events = latest_map(track_id)
            played_after = False
            latest_played_after = None
            if added_at and events:
                for event in events:
                    played_at = parse_ts(event.get("played_at"))
                    if played_at and played_at > added_at:
                        played_after = True
                        if latest_played_after is None or played_at > latest_played_after:
                            latest_played_after = played_at
                        break
            if played_after:
                played_ids.append((track_id, added_at, latest_played_after))
            else:
                unplayed_ids.append(track_id)

        if not played_ids:
            info("No played tracks detected since add time.")
            return

        if max_replace is not None:
            played_ids = played_ids[:max_replace]

        current_set = set(track_ids)
        all_rows = self.repos.conn.execute(
            "SELECT track_id, name, artist_id, spotify_id FROM tracks;"
        ).fetchall()
        candidate_rows = [row for row in all_rows if row["track_id"] not in current_set]

        # Optional ranking using cached embeddings against playlist name.
        similarity_map: Dict[str, float] = {}
        try:
            model_name = os.getenv("SEARCH_EMBEDDING_MODEL", "all-mpnet-base-v2")
            model = EmbeddingModel(model_name)
            query_vec = model.embed([playlist_name])[0]
            query_norm = vector_norm(query_vec) or 1.0
            rows = (
                self.repos.conn.execute(
                    "SELECT track_id, embedding_blob FROM track_embeddings WHERE track_id IN ({})".format(
                        ",".join(["?"] * len(candidate_rows))
                    ),
                    [row["track_id"] for row in candidate_rows],
                ).fetchall()
                if candidate_rows
                else []
            )
            for row in rows:
                vec = decode_vector(row["embedding_blob"])
                denom = vector_norm(vec) * query_norm
                if denom == 0:
                    continue
                similarity = sum(a * b for a, b in zip(vec, query_vec)) / denom
                similarity_map[row["track_id"]] = similarity
        except Exception:
            similarity_map = {}

        # Sort candidates by similarity (desc), fallback to most recently played (None first).
        def candidate_key(row) -> tuple:
            sim = similarity_map.get(row["track_id"])
            if sim is not None:
                return (0, -sim)
            events = self.repos.listen_events.list_by_track(row["track_id"])
            latest_played = None
            for event in events:
                played_at = parse_ts(event.get("played_at"))
                if played_at and (latest_played is None or played_at > latest_played):
                    latest_played = played_at
            return (1, latest_played is not None, latest_played or datetime.min)

        candidate_rows.sort(key=candidate_key)

        needed = min(len(played_ids), len(candidate_rows))
        replacements = candidate_rows[:needed]

        songs_to_keep: List[Song] = []
        for entry in tracks:
            name = entry.get("name") or ""
            artist = entry.get("artist") or ""
            track_id = f"{artist.lower()}|||{name.lower()}" if name and artist else None
            if not track_id or any(track_id == pid for pid, _, _ in played_ids):
                continue
            songs_to_keep.append(
                Song(
                    id=track_id,
                    name=name,
                    artist=artist,
                    spotify_uri=entry.get("uri"),
                    first_added=datetime.now(),
                )
            )

        replacement_songs: List[Song] = []
        for row in replacements:
            artist_record = self.repos.artists.get(row["artist_id"] or "")
            artist_name = artist_record.get("name") if artist_record else row["artist_id"]
            replacement_songs.append(
                Song(
                    id=row["track_id"],
                    name=row["name"],
                    artist=artist_name or "",
                    spotify_uri=row["spotify_id"],
                    first_added=datetime.now(),
                )
            )

        new_songs = songs_to_keep + replacement_songs
        success = self.spotify.replace_playlist_items(playlist_name, new_songs)
        if success:
            info(f"Replaced {len(replacements)} played tracks.")
            subsection("Removed (Played After Added)")
            removed_rows = []
            for idx, (track_id, added_at, played_at) in enumerate(played_ids[:needed], 1):
                track = self.repos.tracks.get(track_id) or {}
                artist_record = self.repos.artists.get(track.get("artist_id") or "")
                artist_name = artist_record.get("name") if artist_record else track.get("artist_id")
                removed_rows.append(
                    [
                        idx,
                        track.get("name") or track_id,
                        artist_name or "",
                        added_at.isoformat() if added_at else "",
                        played_at.isoformat() if played_at else "",
                    ]
                )
            if removed_rows:
                table(["#", "Song", "Artist", "Added At", "Played At"], removed_rows)

            subsection("Added (Replacements)")
            added_rows = []
            for idx, row in enumerate(replacements, 1):
                artist_record = self.repos.artists.get(row["artist_id"] or "")
                artist_name = artist_record.get("name") if artist_record else row["artist_id"]
                added_rows.append(
                    [
                        idx,
                        row.get("name") or "",
                        artist_name or "",
                        row.get("spotify_id") or "",
                    ]
                )
            if added_rows:
                table(["#", "Song", "Artist", "Spotify ID"], added_rows)
        else:
            warning("Failed to update playlist.")

    def build_songs_from_search_results(self, results: List[Dict]) -> List[Song]:
        songs: List[Song] = []
        seen = set()
        for item in results:
            song = self._song_from_result(item)
            if not song:
                continue
            if song.id in seen:
                continue
            seen.add(song.id)
            songs.append(song)
        return songs

    def _parse_metric_number(self, value: object) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().lower().replace(",", "")
        multiplier = 1
        if text.endswith("k"):
            multiplier = 1000
            text = text[:-1]
        elif text.endswith("m"):
            multiplier = 1000000
            text = text[:-1]
        try:
            return float(text) * multiplier
        except ValueError:
            return None

    def _attach_spotify_urls(self, results: List[Dict]) -> None:
        if not results:
            return
        try:
            spotify = self.spotify
        except Exception as e:
            logger.warning("Spotify validation unavailable: %s", e)
            return
        info("Validating Spotify availability...")
        for item in results:
            if item.get("spotify_url") or item.get("spotify_uri"):
                if not item.get("spotify_url") and item.get("spotify_uri"):
                    item["spotify_url"] = self._spotify_url_from_uri(item.get("spotify_uri"))
                continue
            song = self._song_from_result(item)
            if not song:
                continue
            try:
                query = f"track:{song.name} artist:{song.artist}"
                search_results = spotify.sp.search(query, type="track", limit=1)
                items = search_results.get("tracks", {}).get("items", [])
                if not items:
                    item["spotify_url"] = ""
                    continue
                track = items[0]
                item["spotify_uri"] = track.get("uri")
                item["spotify_url"] = track.get("external_urls", {}).get("spotify", "")
                artists = track.get("artists", [])
                if artists:
                    item["spotify_artist_id"] = artists[0].get("id")
            except Exception as exc:
                logger.warning(
                    "Spotify lookup failed for %s by %s: %s", song.name, song.artist, exc
                )

    def _spotify_url_from_uri(self, uri: Optional[str]) -> str:
        if not uri:
            return ""
        if uri.startswith("http"):
            return uri
        if uri.startswith("spotify:track:"):
            track_id = uri.split(":")[-1]
            if track_id:
                return f"https://open.spotify.com/track/{track_id}"
        return uri

    def _obscurity_mode(self) -> str:
        mode = (os.getenv("OBSCURITY_VALIDATION_MODE") or "strict").lower()
        if mode not in {"strict", "followers"}:
            return "strict"
        return mode

    def _similarity_min(self) -> float:
        value = os.getenv("SEARCH_SIMILARITY_MIN", "0.55")
        try:
            return float(value)
        except ValueError:
            return 0.55

    def _audio_similarity_min(self) -> float:
        value = os.getenv("SEARCH_AUDIO_SIMILARITY_MIN", os.getenv("SEARCH_SIMILARITY_MIN", "0.55"))
        try:
            return float(value)
        except ValueError:
            return 0.55

    def _audio_similarity_mode(self) -> str:
        mode = (os.getenv("SEARCH_AUDIO_SIMILARITY_MODE") or "strict").lower()
        if mode not in {"strict", "soft"}:
            return "strict"
        return mode

    def _extract_seed_artists(self, query: str) -> List[str]:
        if not query:
            return []
        patterns = [
            r"like\s+([^,;]+)",
            r"similar to\s+([^,;]+)",
            r"in the style of\s+([^,;]+)",
            r"in the vein of\s+([^,;]+)",
        ]
        seeds: List[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, query, flags=re.IGNORECASE):
                segment = match.group(1)
                segment = re.split(
                    r"\b(with|under|over|below|above|less than|more than|at least|at most|featuring|feat\.?|ft\.?|that|but|for)\b",
                    segment,
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0]
                parts = re.split(r",| and ", segment)
                for part in parts:
                    name = part.strip()
                    if name:
                        seeds.append(name)
        deduped = []
        seen = set()
        for name in seeds:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(name)
        return deduped

    def _seed_songs_from_query(self, query: str) -> List[Song]:
        artists = self._extract_seed_artists(query)
        if not artists:
            return []
        seeds: List[Song] = []
        for artist_name in artists:
            top_tracks = self.spotify.get_artist_top_tracks(artist_name, limit=3)
            for track in top_tracks:
                name = track.get("name")
                artist = track.get("artist") or artist_name
                uri = track.get("uri")
                if not name or not artist:
                    continue
                song = Song(
                    id=f"{artist.lower()}|||{name.lower()}",
                    name=name.lower(),
                    artist=artist.lower(),
                    spotify_uri=uri,
                    first_added=datetime.now(),
                )
                seeds.append(song)
        return seeds

    def _audio_similarity_scores(
        self, candidates: List[Song], seeds: List[Song]
    ) -> Dict[str, float]:
        if not candidates or not seeds:
            return {}
        try:
            from scoring import PlaylistProfile, SpotifyAudioFeaturesProvider
        except Exception as exc:
            logger.warning("Audio similarity unavailable: %s", exc)
            return {}

        provider = SpotifyAudioFeaturesProvider(self.spotify)
        profile = PlaylistProfile(
            playlist_name="search",
            query=self.last_search_query,
            seed_songs=seeds,
            seed_text="; ".join([f"{s.name} by {s.artist}" for s in seeds[:10]]),
        )
        return provider.score_candidates(candidates, profile)

    def _resolve_search_results(self, results: List[Dict]) -> Tuple[List[Song], Dict[str, int]]:
        """Resolve search results into Spotify-validated Song objects."""
        try:
            spotify = self.spotify
        except Exception as e:
            logger.error(f"Failed to initialize Spotify for validation: {str(e)}")
            return [], {"error": len(results) if results else 0}

        constraints = self.last_search_constraints or {}
        require_spotify = (os.getenv("SEARCH_SPOTIFY_REQUIRED") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        max_listeners = constraints.get("max_monthly_listeners")
        min_listeners = constraints.get("min_monthly_listeners")
        similarity_required = bool(constraints.get("similarity_requested"))
        similarity_min = self._similarity_min() if similarity_required else None
        audio_similarity_min = self._audio_similarity_min() if similarity_required else None
        audio_similarity_mode = self._audio_similarity_mode() if similarity_required else "strict"
        obscurity_mode = self._obscurity_mode()

        stats = {
            "total": 0,
            "validated": 0,
            "not_found": 0,
            "popular_artist": 0,
            "spotify_required_failed": 0,
            "obscurity_failed": 0,
            "obscurity_unverified": 0,
            "similarity_failed": 0,
            "similarity_unverified": 0,
            "error": 0,
        }
        validated: List[Song] = []
        seen = set()

        for item in results:
            stats["total"] += 1
            metrics = item.get("metrics") or {}
            song = self._song_from_result(item)
            if not song:
                stats["error"] += 1
                continue
            if song.id in seen:
                continue
            seen.add(song.id)

            if require_spotify and not (item.get("spotify_url") or item.get("spotify_uri")):
                stats["spotify_required_failed"] += 1
                continue

            similarity_value = self._parse_metric_number(metrics.get("similarity"))
            if similarity_required:
                if similarity_value is None:
                    stats["similarity_unverified"] += 1
                    continue
                if similarity_value > 1 and similarity_value <= 100:
                    similarity_value = similarity_value / 100.0
                if similarity_min is not None and float(similarity_value) < similarity_min:
                    stats["similarity_failed"] += 1
                    continue

            monthly_listeners = self._parse_metric_number(metrics.get("monthly_listeners"))
            pending_obscurity_proxy = False
            if max_listeners or min_listeners:
                if monthly_listeners is not None:
                    if max_listeners and monthly_listeners > max_listeners:
                        stats["obscurity_failed"] += 1
                        continue
                    if min_listeners and monthly_listeners < min_listeners:
                        stats["obscurity_failed"] += 1
                        continue
                else:
                    if obscurity_mode == "strict":
                        stats["obscurity_unverified"] += 1
                        continue
                    pending_obscurity_proxy = True

            try:
                artist_id = item.get("spotify_artist_id")
                if item.get("spotify_uri"):
                    song.spotify_uri = item.get("spotify_uri")

                if not artist_id:
                    if require_spotify and not (item.get("spotify_url") or item.get("spotify_uri")):
                        stats["spotify_required_failed"] += 1
                        continue
                    query = f"track:{song.name} artist:{song.artist}"
                    search_results = spotify.sp.search(query, type="track", limit=1)
                    if not search_results.get("tracks", {}).get("items", []):
                        stats["not_found"] += 1
                        continue

                    track = search_results.get("tracks", {}).get("items", [])[0]
                    song.spotify_uri = track["uri"]
                    if not track.get("artists"):
                        stats["not_found"] += 1
                        continue
                    artist_id = track["artists"][0]["id"]

                artist_info = spotify.sp.artist(artist_id)
                follower_count = (artist_info.get("followers") or {}).get("total", 0)

                if pending_obscurity_proxy:
                    if max_listeners and follower_count > max_listeners:
                        stats["obscurity_failed"] += 1
                        continue
                    if min_listeners and follower_count < min_listeners:
                        stats["obscurity_failed"] += 1
                        continue

                if follower_count >= 1000000:
                    stats["popular_artist"] += 1
                    continue

                validated.append(song)
                stats["validated"] += 1
            except Exception as e:
                logger.warning(f"Error processing {song.name} by {song.artist}: {str(e)}")
                stats["error"] += 1

        if similarity_required and validated:
            seed_songs = self._seed_songs_from_query(self.last_search_query or "")
            if not seed_songs:
                logger.warning("No seed artists found for audio similarity validation.")
            audio_scores = (
                self._audio_similarity_scores(validated, seed_songs) if seed_songs else {}
            )

            filtered: List[Song] = []
            for song in validated:
                score = audio_scores.get(song.id)
                if score is None:
                    stats["similarity_unverified"] += 1
                    if audio_similarity_mode == "strict":
                        continue
                    filtered.append(song)
                    continue
                if audio_similarity_min is not None and score < audio_similarity_min:
                    stats["similarity_failed"] += 1
                    continue
                filtered.append(song)

            stats["validated"] = len(filtered)
            validated = filtered

        return validated, stats

    def resolve_search_results_for_playlist(self, results: List[Dict]) -> List[Song]:
        songs, validation_stats = self._resolve_search_results(results)
        summary_stats = {
            "total": validation_stats.get("total", 0),
            "validated": validation_stats.get("validated", 0),
            "not_found": validation_stats.get("not_found", 0),
            "popular_artist": validation_stats.get("popular_artist", 0),
            "obscurity_failed": validation_stats.get("obscurity_failed", 0),
            "obscurity_unverified": validation_stats.get("obscurity_unverified", 0),
            "similarity_failed": validation_stats.get("similarity_failed", 0),
            "similarity_unverified": validation_stats.get("similarity_unverified", 0),
            "error": validation_stats.get("error", 0),
        }
        self._show_search_validation_summary(summary_stats, title="Search Validation Summary")
        return songs

    def mark_search_tracks(
        self,
        track_ids: List[str],
        status: str,
        reason: Optional[str] = None,
    ) -> None:
        """Mark tracks from a search run as accepted/rejected."""
        if not track_ids:
            return
        now = datetime.utcnow().isoformat() + "Z"
        for track_id in track_ids:
            self.repos.tracks.update_status(track_id, status, reason, now)
        self.repos.conn.commit()

    def _songs_from_track_ids(self, track_ids: List[str]) -> List[Song]:
        """Resolve cached track IDs into Song objects, skipping any unknown IDs."""
        songs: List[Song] = []
        for track_id in track_ids:
            record = self.repos.tracks.get(track_id)
            if not record:
                continue
            artist_name = record.get("artist_id") or ""
            artist_record = self.repos.artists.get(record.get("artist_id") or "")
            if artist_record and artist_record.get("name"):
                artist_name = artist_record.get("name")
            songs.append(
                Song(
                    id=track_id,
                    name=record.get("name") or "",
                    artist=artist_name,
                    spotify_uri=record.get("spotify_id"),
                    first_added=datetime.now(),
                )
            )
        return songs

    def add_search_to_playlist(
        self,
        playlist_name: str,
        track_ids: List[str],
        *,
        replace: bool = False,
    ) -> bool:
        """Add cached search-result tracks to a Spotify playlist.

        This is the single add-path: the interactive wizard and the headless
        `/search --to` flags both route through here, so /undo only has to snapshot
        in one place.

        Two non-destructive modes:
          * append (default) — add the tracks, leaving any existing ones in place.
          * replace          — swap the playlist's contents while keeping the same
            playlist (and its Spotify ID, URL, and followers).

        The destructive delete-and-recreate path (refresh_playlist) is never used
        here, so a typo'd NAME can at worst create/append to a playlist — it can
        never wipe an unrelated playlist's identity.
        """
        if not track_ids:
            warning("No tracks available to add.")
            return False
        songs = self._songs_from_track_ids(track_ids)
        if not songs:
            warning("None of the search results could be resolved to tracks.")
            return False
        # Snapshot the current contents *before* mutating, so /undo can restore them.
        prior = self._snapshot_playlist(playlist_name)
        section("Replace Playlist" if replace else "Add to Playlist", playlist_name)
        if replace:
            info(f"Swapping the contents of '{playlist_name}' (the playlist itself is kept).")
            success = self.spotify.replace_playlist_items(playlist_name, songs)
        else:
            success = self.spotify.append_to_playlist(playlist_name, songs)
        if success:
            # Persist any Spotify URIs discovered while matching the tracks.
            self.db._save_state()
            self._record_undo(playlist_name, prior)
            verb = "Replaced" if replace else "Added"
            info(f"{verb} {len(songs)} track(s) in playlist '{playlist_name}'.")
            info("Run /undo to revert this change.")
        else:
            warning(f"Failed to update playlist '{playlist_name}'.")
        return success

    def _snapshot_playlist(self, playlist_name: str) -> List[Dict[str, Any]]:
        """Capture a playlist's current tracks (name/artist/uri) for undo.

        Returns an empty list if the playlist doesn't exist yet (a write that
        creates it) — undoing such a write then correctly clears it.
        """
        try:
            return list(self.spotify.get_playlist_tracks(playlist_name) or [])
        except Exception as exc:  # pragma: no cover - defensive; Spotify call
            logger.warning(f"Could not snapshot '{playlist_name}' for undo: {exc}")
            return []

    def _record_undo(self, playlist_name: str, tracks: List[Dict[str, Any]]) -> None:
        """Push a pre-write snapshot onto the session undo stack."""
        self._undo_stack.append({"playlist": playlist_name, "tracks": list(tracks)})

    def undo_last_write(self) -> bool:
        """Restore the most recent playlist write made this session.

        Uses the ID-preserving replace, so undo keeps the same playlist — it never
        deletes and recreates it. The snapshot is popped only when the restore
        succeeds, so a failed undo can be retried.
        """
        if not self._undo_stack:
            info("Nothing to undo.")
            return False
        entry = self._undo_stack[-1]
        playlist_name = entry["playlist"]
        tracks = entry["tracks"]
        section("Undo", playlist_name)
        songs = [
            Song(
                id=track_id_for(track.get("artist") or "", track.get("name") or ""),
                name=track.get("name") or "",
                artist=track.get("artist") or "",
                spotify_uri=track.get("uri"),
                first_added=datetime.now(),
            )
            for track in tracks
        ]
        success = self.spotify.replace_playlist_items(playlist_name, songs)
        if success:
            self._undo_stack.pop()
            if tracks:
                info(f"Restored '{playlist_name}' to its previous {len(tracks)} track(s).")
            else:
                info(f"Cleared '{playlist_name}' (it had no tracks before the last change).")
        else:
            warning(f"Failed to undo the last change to '{playlist_name}'.")
        return success

    def enrich_library(self, limit: int = 25, dry_run: bool = False, concurrency: int = 8) -> int:
        """Backfill semantic context + re-embed library tracks that lack context.

        Each track is a real deep-search call, so this is bounded by `limit`
        (default 25) — a full-library backfill is an explicit, larger `--limit`.
        The slow network fetch is parallelized across `concurrency` workers while
        DB writes stay serialized. Tracks whose deep-search results don't surface
        them are left untouched. Returns the number of tracks enriched.
        """
        section("Enrich Library")
        info("Semantic context only (genre/mood/era/style) — not acoustic audio features.")
        rows = self.repos.conn.execute(
            """
            SELECT t.track_id, t.name, t.artist_id, a.name AS artist_name
            FROM tracks t
            LEFT JOIN track_context c ON c.track_id = t.track_id
            LEFT JOIN artists a ON a.artist_id = t.artist_id
            WHERE c.track_id IS NULL
            ORDER BY t.track_id
            LIMIT ?
            """,
            (max(0, limit),),
        ).fetchall()
        if not rows:
            info("Nothing to enrich — every track already has context.")
            return 0

        def _name_artist(row: Any) -> Tuple[str, str]:
            return (row["name"] or "", row["artist_name"] or row["artist_id"] or "")

        total = len(rows)
        info(f"Found {total} track(s) without context (limit {limit}).")
        if dry_run:
            for row in rows:
                name, artist = _name_artist(row)
                info(f"  would enrich: {name} — {artist}")
            info(f"Dry run: {total} track(s) would be enriched. Re-run without --dry-run.")
            return 0

        pipeline = self.search_pipeline  # single source of truth for model + thresholds
        workers = max(1, min(concurrency, total))
        info(f"Fetching across {workers} parallel worker(s); writes are serialized.")

        def _on_result(status: str, name: str, artist: str) -> None:
            if status == "enriched":
                info(f"  enriched: {name} — {artist}")
            elif status == "skipped":
                info(f"  no match, left as-is: {name} — {artist}")
            else:
                warning(f"  failed: {name} — {artist}")

        counts = enrich_tracks(
            self.repos,
            [(row["track_id"], *_name_artist(row)) for row in rows],
            model_name=pipeline.model_name,
            strict_threshold=pipeline.strict_threshold,
            lenient_threshold=pipeline.lenient_threshold,
            concurrency=concurrency,
            on_result=_on_result,
        )
        key_value_table(
            [
                ["Enriched", counts["enriched"]],
                ["No match (skipped)", counts["skipped"]],
                ["Failed", counts["failed"]],
            ]
        )
        return counts["enriched"]

    def sonic_backfill(self, limit: int = 50, dry_run: bool = False) -> int:
        """Backfill acoustic features from AcousticBrainz for tracks that lack them.

        Resolves each track to a MusicBrainz MBID and stores its precomputed
        AcousticBrainz sonic vector — no audio downloaded. SERIAL + rate-limited
        (MusicBrainz ~1 req/sec), so a full library is ~minutes, not parallelizable.
        Returns the number of tracks for which sonic features were stored.
        """
        section("Sonic Backfill")
        info("Acoustic features from AcousticBrainz (MBID-keyed; no audio downloaded).")
        rows = self.repos.conn.execute(
            """
            SELECT t.track_id, t.name, a.name AS artist
            FROM tracks t
            LEFT JOIN track_sonic s ON s.track_id = t.track_id
            LEFT JOIN artists a ON a.artist_id = t.artist_id
            WHERE s.track_id IS NULL
            ORDER BY t.track_id
            LIMIT ?
            """,
            (max(0, limit),),
        ).fetchall()
        if not rows:
            info("Nothing to backfill — every track already has sonic features.")
            return 0

        def _name_artist(row: Any) -> Tuple[str, str]:
            return (row["name"] or "", row["artist"] or "")

        total = len(rows)
        info(f"{total} track(s) without sonic features (limit {limit}).")
        if dry_run:
            for row in rows:
                name, artist = _name_artist(row)
                info(f"  would resolve: {name} — {artist}")
            info(f"Dry run: {total} track(s) would be looked up. Re-run without --dry-run.")
            return 0

        info("Resolving via MusicBrainz (~1 req/sec — rate-limited, not parallelizable).")

        def _on_result(status: str, name: str, artist: str) -> None:
            if status == "stored":
                info(f"  stored: {name} — {artist}")
            elif status == "failed":
                warning(f"  failed: {name} — {artist}")
            else:  # no_mbid / no_data
                info(f"  {status.replace('_', ' ')}: {name} — {artist}")

        counts = backfill_sonic(
            self.repos,
            [(row["track_id"], *_name_artist(row)) for row in rows],
            on_result=_on_result,
        )
        key_value_table(
            [
                ["Stored sonic", counts["stored"]],
                ["No MBID match", counts["no_mbid"]],
                ["No AcousticBrainz data", counts["no_data"]],
                ["Failed", counts["failed"]],
            ]
        )
        return counts["stored"]

    def debug_last_search(self) -> Optional[Dict[str, object]]:
        """Return debug payload for the last search run."""
        run_id = self.last_search_run_id
        if not run_id:
            return None
        run = self.repos.runs.get(run_id)
        candidates = self.repos.candidates.list_by_run(run_id)
        strict_ratios = []
        missing_context = 0
        for candidate in candidates:
            track = self.repos.tracks.get(candidate["track_id"])
            if track:
                artist_record = self.repos.artists.get(track.get("artist_id") or "")
                if artist_record and artist_record.get("name"):
                    track["artist_name"] = artist_record.get("name")
                candidate["track"] = track
            context = self.repos.context.get(candidate["track_id"])
            if context:
                ratio = context.get("strict_ratio")
                if ratio is not None:
                    strict_ratios.append(float(ratio))
                if not context.get("context_text"):
                    missing_context += 1
            else:
                missing_context += 1
        avg_strict = sum(strict_ratios) / len(strict_ratios) if strict_ratios else 0.0
        score_config = getattr(self.search_pipeline, "last_score_config", None)
        return {
            "run": run,
            "candidates": candidates,
            "summary": {
                "count": len(candidates),
                "avg_strict_ratio": avg_strict,
                "missing_context": missing_context,
                "cached": bool(self.last_search_cached),
                "model_name": getattr(self.search_pipeline, "model_name", None),
                "score_config": asdict(score_config) if score_config else None,
            },
        }

    def debug_track(self, track_id: str) -> Optional[Dict[str, object]]:
        """Return track + context + sources for debug display."""
        raw_target = track_id.strip()
        resolved_rank = None
        if raw_target.isdigit() and self.last_search_results:
            rank = int(raw_target)
            if 1 <= rank <= len(self.last_search_results):
                entry = self.last_search_results[rank - 1]
                track_id = entry.get("track_id") or track_id
                resolved_rank = rank
        record = self.repos.tracks.get(track_id)
        if not record:
            return None
        context = self.repos.context.get(track_id)
        sources = list(self.repos.sources.list_by_track(track_id))
        embedding = self.repos.embeddings.get(track_id)
        listens = list(self.repos.listen_events.list_by_track(track_id))
        payload = {
            "track": record,
            "context": context,
            "sources": sources,
            "embedding": embedding,
            "listens": listens,
        }
        if resolved_rank:
            payload["resolved_rank"] = resolved_rank
        return payload

    def _show_search_validation_summary(self, stats: Dict[str, int], title: str) -> None:
        section(title)
        rows = [["Total results processed", stats.get("total", 0)]]
        if "validated" in stats:
            rows.append(["Validated by Spotify", stats.get("validated", 0)])
        if "spotify_required_failed" in stats:
            rows.append(["Spotify required (missing URL)", stats.get("spotify_required_failed", 0)])
        if "added" in stats:
            rows.append(["Songs added", stats.get("added", 0)])
        if "already_exists" in stats:
            rows.append(["Songs already in database", stats.get("already_exists", 0)])
        rows.extend(
            [
                ["Songs not found in Spotify", stats.get("not_found", 0)],
                ["Artists with >=1M followers", stats.get("popular_artist", 0)],
                ["Obscurity failed", stats.get("obscurity_failed", 0)],
                ["Obscurity unverified", stats.get("obscurity_unverified", 0)],
                ["Similarity failed", stats.get("similarity_failed", 0)],
                ["Similarity unverified", stats.get("similarity_unverified", 0)],
                ["Errors", stats.get("error", 0)],
            ]
        )
        key_value_table(rows)

    def _song_from_result(self, item: Dict) -> Optional[Song]:
        name = (item.get("song") or item.get("name") or "").strip()
        artist = (item.get("artist") or "").strip()
        if not name or not artist:
            return None
        return Song(
            id=f"{artist.lower()}|||{name.lower()}",
            name=name,
            artist=artist,
            first_added=datetime.now(),
        )

    def plan_playlist(
        self,
        playlist_name: str,
        song_count: int,
        fresh_days: int,
        generations: int,
        score_strategy: str = "local",
        query: Optional[str] = None,
    ):
        """Preview future generations without updating Spotify."""
        try:
            rm = self._get_rotation_manager(playlist_name)
            score_config = PlaylistScoreConfig(strategy=score_strategy, query=query)
            plans = rm.simulate_generations(
                count=song_count,
                fresh_days=fresh_days,
                generations=generations,
                score_config=score_config,
            )
            section("Plan", f"{generations} future generations for '{playlist_name}'")
            for idx, songs in enumerate(plans, 1):
                subsection(f"Generation {idx}")
                table_data = [[i, s.name, s.artist] for i, s in enumerate(songs, 1)]
                table(["#", "Song", "Artist"], table_data)
        except Exception as e:
            logger.error(f"Error planning playlist: {str(e)}")

    def diff_playlist(
        self,
        playlist_name: str,
        song_count: int,
        fresh_days: int,
        score_strategy: str = "local",
        query: Optional[str] = None,
    ):
        """Show playlist changes before applying update."""
        try:
            rm = self._get_rotation_manager(playlist_name)
            logger.info(f"Selecting {song_count} songs for diff (fresh_days={fresh_days})...")
            score_config = PlaylistScoreConfig(strategy=score_strategy, query=query)
            selected = rm.select_songs_for_today(
                count=song_count, fresh_days=fresh_days, score_config=score_config
            )

            current_tracks = self.spotify.get_playlist_tracks(playlist_name)
            current_uris = {t["uri"] for t in current_tracks if t.get("uri")}

            selected_uris = set()
            for song in selected:
                if not song.spotify_uri:
                    song.spotify_uri = self.spotify.search_song(song)
                if song.spotify_uri:
                    selected_uris.add(song.spotify_uri)

            to_add = selected_uris - current_uris
            to_remove = current_uris - selected_uris

            section("Playlist Diff")
            key_value_table(
                [
                    ["Would add", f"{len(to_add)} tracks"],
                    ["Would remove", f"{len(to_remove)} tracks"],
                ]
            )

            if to_add:
                add_sample = []
                for uri in list(to_add)[:10]:
                    add_sample.append([uri])
                subsection("Sample additions (URIs)")
                table(["URI"], add_sample)

            if to_remove:
                remove_sample = []
                for uri in list(to_remove)[:10]:
                    remove_sample.append([uri])
                subsection("Sample removals (URIs)")
                table(["URI"], remove_sample)
        except Exception as e:
            logger.error(f"Error generating playlist diff: {str(e)}")

    def auth_status(self):
        """Show Spotify auth token status without triggering auth flow."""
        token_info = get_cached_token_info()
        if not token_info:
            info("No cached Spotify token found.")
            return

        expires_at = token_info.get("expires_at")
        expires_in = token_info.get("expires_in")
        scope = token_info.get("scope")

        section("Spotify Auth Status")
        rows = []
        if expires_at:
            expires_dt = datetime.fromtimestamp(expires_at)
            rows.append(["Expires at", expires_dt.isoformat()])
        if expires_in:
            rows.append(["Expires in (seconds)", expires_in])
        if scope:
            rows.append(["Scopes", scope])

        if rows:
            key_value_table(rows)
        else:
            info("Token metadata not available.")

    def auth_refresh(self):
        """Refresh Spotify auth token if possible."""
        refreshed = refresh_cached_token()
        if not refreshed:
            warning(
                "No token refreshed. You may need to re-authenticate using any Spotify command."
            )
            return
        expires_at = refreshed.get("expires_at")
        if expires_at:
            expires_dt = datetime.fromtimestamp(expires_at)
            info(f"Token refreshed. New expiry: {expires_dt.isoformat()}")
        else:
            info("Token refreshed.")


def main():
    if len(sys.argv) > 1:
        print("Classic CLI has been removed. Use 'tunr' or run without arguments.")
        return 1

    try:
        from interactive_app import run_interactive
    except ImportError:
        print("Interactive mode requires the 'textual' package.")
        print("Install dependencies and try again.")
        return 1
    return run_interactive()


# ---------------------------------------------------------------------------
# Command registry
#
# Each command is handled by a small module-level function that pulls the
# relevant fields off the parsed argparse Namespace and delegates to a
# PlaylistCLI method, returning an exit code (0 success, 1 error). The
# `_COMMAND_HANDLERS` mapping (defined below the handlers) replaces what used
# to be a ~200-line if/elif ladder. The public contract
# (`dispatch_command(cli, command, args) -> int`, the cli method signatures,
# and the return codes) is unchanged and locked by tests/test_dispatch_command.py.
#
# NOTE (future step): per-domain service-object extraction (search / rotation /
# ingest / maintenance / auth) is deliberately deferred — the cli methods are
# locked by the test suite, so wrapping them is high-risk churn. Tracked in
# docs/REMEDIATION_PLAN.md.
# ---------------------------------------------------------------------------


def _handle_import(cli: "PlaylistCLI", args: Any) -> int:
    cli.import_songs(args.file)
    return 0


def _handle_update(cli: "PlaylistCLI", args: Any) -> int:
    cli.update_playlist(
        args.playlist,
        args.count,
        args.fresh_days,
        args.dry_run,
        args.score_strategy,
        args.query,
    )
    return 0


def _handle_stats(cli: "PlaylistCLI", args: Any) -> int:
    if hasattr(args, "output") and args.output and not args.export:
        logger.warning("--output requires --export; ignoring --output")
    if args.export:
        cli.export_stats(args.playlist, args.export, args.output)
    else:
        cli.show_stats(args.playlist)
    return 0


def _handle_profile(cli: "PlaylistCLI", args: Any) -> int:
    json_mode = getattr(args, "json", False)
    set_json_mode(json_mode)
    try:
        payload = cli.show_profile(args.top)
    finally:
        if json_mode:
            emit_json(payload)
        set_json_mode(False)
    return 0


def _handle_taste(cli: "PlaylistCLI", args: Any) -> int:
    json_mode = getattr(args, "json", False)
    set_json_mode(json_mode)
    try:
        payload = cli.show_taste(args.top)
    finally:
        if json_mode:
            emit_json(payload)
        set_json_mode(False)
    return 0


def _handle_view(cli: "PlaylistCLI", args: Any) -> int:
    cli.view_playlist(args.playlist)
    return 0


def _handle_sync(cli: "PlaylistCLI", args: Any) -> int:
    cli.sync_playlist(args.playlist)
    return 0


def _handle_extract(cli: "PlaylistCLI", args: Any) -> int:
    cli.extract_playlist(args.playlist, args.output)
    return 0


def _handle_plan(cli: "PlaylistCLI", args: Any) -> int:
    cli.plan_playlist(
        args.playlist,
        args.count,
        args.fresh_days,
        args.generations,
        args.score_strategy,
        args.query,
    )
    return 0


def _handle_diff(cli: "PlaylistCLI", args: Any) -> int:
    cli.diff_playlist(args.playlist, args.count, args.fresh_days, args.score_strategy, args.query)
    return 0


def _handle_clean(cli: "PlaylistCLI", args: Any) -> int:
    cli.clean_database(args.dry_run)
    return 0


def _handle_search(cli: "PlaylistCLI", args: Any) -> int:
    json_mode = getattr(args, "json", False)
    set_json_mode(json_mode)
    try:
        cli.search_songs(args.query)
        track_ids = cli.last_search_track_ids or []
        handled = False
        if track_ids:
            if getattr(args, "save", False):
                cli.mark_search_tracks(track_ids, status="accepted")
                info(f"Marked {len(track_ids)} result(s) as accepted.")
                handled = True
            to_playlist = getattr(args, "to_playlist", None)
            if to_playlist:
                limit = getattr(args, "limit", None)
                chosen = track_ids[:limit] if limit else track_ids
                cli.add_search_to_playlist(
                    to_playlist, chosen, replace=getattr(args, "replace", False)
                )
                handled = True
            # Tell the interactive UI the results are already dealt with, so it
            # doesn't also pop the yes/no -> db/playlist -> name follow-up prompts.
            if handled:
                cli.last_search_handled = True
    finally:
        if json_mode:
            emit_json(
                {
                    "query": cli.last_search_query,
                    "count": len(cli.last_search_results or []),
                    "results": cli.last_search_results or [],
                }
            )
        set_json_mode(False)
    return 0


def _handle_find(cli: "PlaylistCLI", args: Any) -> int:
    """Flagship: deep search, re-ranked by taste, optionally written to a playlist.

    Composes search_songs (#search) -> taste_rank_last_search (taste centroid) ->
    add_search_to_playlist (guarded, undoable). The intermediate search rendering
    is suppressed so /find shows only the re-ranked view.
    """
    want_json = getattr(args, "json", False)
    weight = max(0.0, min(1.0, getattr(args, "taste_weight", 0.5)))
    to_playlist = getattr(args, "to_playlist", None)
    limit = getattr(args, "limit", None)
    replace = getattr(args, "replace", False)

    # Run the search quietly — /find presents the re-ranked list, not the raw search.
    set_json_mode(True)
    ranked: List[Dict[str, Any]] = []
    signal = ""
    try:
        cli.search_songs(args.query)
        ranked, signal = cli.taste_rank_last_search(taste_weight=weight)
    finally:
        if not want_json:
            set_json_mode(False)

    def _chosen_ids() -> List[str]:
        ids = [r["track_id"] for r in ranked if r.get("track_id")]
        return ids[:limit] if limit else ids

    if want_json:
        wrote = None
        if to_playlist and ranked:
            chosen = _chosen_ids()
            ok = cli.add_search_to_playlist(to_playlist, chosen, replace=replace)
            cli.last_search_handled = True
            wrote = {"playlist": to_playlist, "requested": len(chosen), "ok": bool(ok)}
        emit_json(
            {
                "query": cli.last_search_query,
                "taste_weight": weight,
                "signal": signal,
                "count": len(ranked),
                "results": ranked,
                "wrote": wrote,
            }
        )
        set_json_mode(False)
        return 0

    if not ranked:
        info("No results to rank.")
        return 0
    section("Find", cli.last_search_query)
    info(f"Blend: {round(weight * 100)}% taste · {round((1 - weight) * 100)}% relevance — {signal}")
    table(
        ["#", "Song", "Artist", "Year", "Rel", "Taste", "Blend"],
        [
            [
                i,
                r["song"],
                r["artist"],
                r["year"] or "-",
                f"{r['rel_norm']:.2f}",
                f"{r['taste_norm']:.2f}",
                f"{r['blended']:.2f}",
            ]
            for i, r in enumerate(ranked, 1)
        ],
    )
    if to_playlist:
        cli.add_search_to_playlist(to_playlist, _chosen_ids(), replace=replace)
        cli.last_search_handled = True
    else:
        info("Preview only. Re-run with --to NAME to add these to a playlist.")
    return 0


def _handle_undo(cli: "PlaylistCLI", args: Any) -> int:
    cli.undo_last_write()
    return 0


def _handle_enrich(cli: "PlaylistCLI", args: Any) -> int:
    cli.enrich_library(
        limit=getattr(args, "limit", 25),
        dry_run=getattr(args, "dry_run", False),
        concurrency=getattr(args, "concurrency", 8),
    )
    return 0


def _handle_sonic(cli: "PlaylistCLI", args: Any) -> int:
    cli.sonic_backfill(limit=getattr(args, "limit", 50), dry_run=getattr(args, "dry_run", False))
    return 0


def _present_debug_track(payload: dict) -> None:
    """Render the `debug track` payload as tables (output unchanged)."""
    track = payload.get("track") or {}
    context = payload.get("context") or {}
    sources = payload.get("sources") or []
    embedding = payload.get("embedding") or {}
    listens = payload.get("listens") or []
    section("Debug", "Track")
    rows = [
        ["Track ID", track.get("track_id") or ""],
        ["Name", track.get("name") or ""],
        ["Artist ID", track.get("artist_id") or ""],
        ["Spotify ID", track.get("spotify_id") or ""],
        ["Spotify URL", track.get("spotify_url") or ""],
        ["Release", track.get("release_date") or ""],
        ["Status", track.get("status") or ""],
    ]
    if payload.get("resolved_rank"):
        rows.append(["Resolved Rank", payload.get("resolved_rank")])
    key_value_table(rows)
    if context:
        subsection("Context")
        key_value_table(
            [
                ["Strict Ratio", context.get("strict_ratio")],
                ["Context Text", (context.get("context_text") or "")[:200]],
            ]
        )
    if sources:
        subsection("Sources")
        table(
            ["#", "URL", "Title", "Snippet", "Provider", "Strict"],
            [
                [
                    idx,
                    s.get("url") or "",
                    s.get("title") or "",
                    s.get("snippet") or "",
                    s.get("provider") or "",
                    "yes" if s.get("is_strict") else "no",
                ]
                for idx, s in enumerate(sources, 1)
            ],
        )
    if embedding:
        subsection("Embedding")
        key_value_table(
            [
                ["Model", embedding.get("model_name") or ""],
                ["Dimensions", embedding.get("embedding_dim") or ""],
                ["Norm", embedding.get("embedding_norm")],
            ]
        )
    if listens:
        subsection("Listen Events")
        table(
            ["#", "Played At", "Source"],
            [
                [idx, event.get("played_at") or "", event.get("source") or ""]
                for idx, event in enumerate(listens[:10], 1)
            ],
        )


def _present_debug_last_search(payload: dict) -> None:
    """Render the `debug last-search` payload as tables (output unchanged)."""
    run = payload.get("run") or {}
    candidates = payload.get("candidates") or []
    summary = payload.get("summary") or {}
    section("Debug", "Last Search")
    key_value_table(
        [
            ["Run ID", run.get("run_id")],
            ["Started", run.get("started_at")],
            ["Finished", run.get("finished_at")],
            ["Status", run.get("status")],
            ["Results", len(candidates)],
            ["Cached", summary.get("cached")],
            ["Avg strict ratio", f"{summary.get('avg_strict_ratio', 0):.2f}"],
            ["Missing context", summary.get("missing_context", 0)],
            ["Model", summary.get("model_name") or ""],
        ]
    )
    score_config = summary.get("score_config") or {}
    if score_config:
        subsection("Score Config")
        key_value_table(
            [
                ["Base", score_config.get("base_weight")],
                ["Strict", score_config.get("strict_weight")],
                ["Source", score_config.get("source_weight")],
                ["Year", score_config.get("year_weight")],
                ["Year tol", score_config.get("year_tolerance")],
                ["Source cap", score_config.get("source_cap")],
                ["Year target", score_config.get("year_target")],
            ]
        )
    if candidates:
        preview_rows = []
        for idx, candidate in enumerate(candidates[:10], 1):
            track = candidate.get("track") or {}
            artist_label = track.get("artist_name") or track.get("artist_id") or ""
            label = f"{track.get('name', '')} — {artist_label}".strip(" —")
            preview_rows.append([idx, label, candidate.get("track_id") or ""])
        subsection("Top Results (IDs)")
        table(["#", "Track", "Track ID"], preview_rows)


def _present_debug(payload: dict, topic: str) -> None:
    """Render a debug payload as tables, dispatching on topic."""
    if topic == "track":
        _present_debug_track(payload)
    else:
        _present_debug_last_search(payload)


def _handle_debug(cli: "PlaylistCLI", args: Any) -> int:
    topic = getattr(args, "topic", "last")
    fmt = getattr(args, "format", "json")
    if topic == "track":
        if not getattr(args, "value", None):
            warning("Track ID required for debug track.")
            return 1
        payload = cli.debug_track(args.value)
    else:
        payload = cli.debug_last_search()
    if not payload:
        warning("No debug data available.")
        return 1
    if fmt == "table":
        _present_debug(payload, topic)
        return 0
    json_output(payload)
    return 0


def _handle_ingest(cli: "PlaylistCLI", args: Any) -> int:
    cli.ingest_tracks(args.source, args.name, args.time_range)
    return 0


def _handle_listen_sync(cli: "PlaylistCLI", args: Any) -> int:
    cli.sync_listen_history(args.limit)
    return 0


def _handle_rotate_played(cli: "PlaylistCLI", args: Any) -> int:
    # Deprecated alias for `rotate`; kept one release so existing muscle memory
    # and scripts get a redirect instead of an "unknown command" error.
    logger.warning("`rotate-played` is deprecated — use `rotate` instead.")
    cli.rotate_playlist_played(args.playlist, args.max_replace)
    return 0


def _handle_rotate(cli: "PlaylistCLI", args: Any) -> int:
    cli.rotate_playlist_played(args.playlist, args.max_replace)
    return 0


def _handle_backup(cli: "PlaylistCLI", args: Any) -> int:
    cli.backup_data(args.backup_name)
    return 0


def _handle_restore(cli: "PlaylistCLI", args: Any) -> int:
    cli.restore_data(args.backup_name)
    return 0


def _handle_restore_previous_rotation(cli: "PlaylistCLI", args: Any) -> int:
    cli.restore_previous_rotation(args.playlist, args.offset)
    return 0


def _handle_list_rotations(cli: "PlaylistCLI", args: Any) -> int:
    cli.list_rotations(args.playlist, args.generations)
    return 0


def _handle_list_backups(cli: "PlaylistCLI", args: Any) -> int:
    cli.list_backups()
    return 0


def _handle_auth_status(cli: "PlaylistCLI", args: Any) -> int:
    cli.auth_status()
    return 0


def _handle_auth_refresh(cli: "PlaylistCLI", args: Any) -> int:
    cli.auth_refresh()
    return 0


def _handle_interactive(cli: "PlaylistCLI", args: Any) -> int:
    logger.info("Already running. Use the interactive UI directly.")
    return 0


# Built after the handler functions so each name is already defined.
_COMMAND_HANDLERS: Dict[str, Callable[["PlaylistCLI", Any], int]] = {
    "import": _handle_import,
    "update": _handle_update,
    "stats": _handle_stats,
    "profile": _handle_profile,
    "taste": _handle_taste,
    "view": _handle_view,
    "sync": _handle_sync,
    "extract": _handle_extract,
    "plan": _handle_plan,
    "diff": _handle_diff,
    "clean": _handle_clean,
    "search": _handle_search,
    "find": _handle_find,
    "undo": _handle_undo,
    "enrich": _handle_enrich,
    "sonic": _handle_sonic,
    "debug": _handle_debug,
    "ingest": _handle_ingest,
    "listen-sync": _handle_listen_sync,
    "rotate-played": _handle_rotate_played,
    "rotate": _handle_rotate,
    "backup": _handle_backup,
    "restore": _handle_restore,
    "restore-previous-rotation": _handle_restore_previous_rotation,
    "list-rotations": _handle_list_rotations,
    "list-backups": _handle_list_backups,
    "auth-status": _handle_auth_status,
    "auth-refresh": _handle_auth_refresh,
    "interactive": _handle_interactive,
}


def dispatch_command(cli: "PlaylistCLI", command: str, args: object) -> int:
    """Execute a parsed command against the CLI via the command registry."""
    try:
        handler = _COMMAND_HANDLERS.get(command)
        if handler is None:
            logger.error(f"Unknown command: {command}")
            return 1
        return handler(cli, args)
    except Exception as e:
        logger.error(f"Command failed: {str(e)}")
        return 1


if __name__ == "__main__":
    exit(main())
