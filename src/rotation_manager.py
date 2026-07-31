import logging
import random
import re as _re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from models import PlaylistHistory, RotationStats, Song
from plays import last_played_map, parse_played_at, recency_weights
from scoring import MatchScorer, PlaylistScoreConfig
from spotify_manager import SpotifyManager
from ui import caption

logger = logging.getLogger(__name__)

# Within-tier demotion strength: how much one unit of recency-weighted play
# mass (plays.recency_weights — a play right now weighs 1.0) subtracts from a
# candidate's ranking score. Match scores are cosine-similarity scale (~[0,1]),
# so a couple of recent real plays demote a track without ejecting it.
LISTEN_DEMOTION_WEIGHT = 0.05


def _playlist_slug(playlist_name: str) -> str:
    return _re.sub(r"[^a-z0-9_-]", "_", playlist_name.lower())


class RotationManager:
    """Manages the rotation of songs in playlists"""

    def __init__(
        self,
        playlist_name: str,
        db: Any,
        spotify: SpotifyManager,
        repos: Any = None,
    ):
        self.playlist_name = playlist_name
        self.db = db
        self.spotify = spotify
        self.repos = repos

        # Get project root directory
        self.root_dir = Path(__file__).parent.parent

        # Load or create history
        self.history = self._load_history()
        if not self.history:
            logger.info(f"Creating new history for playlist '{playlist_name}'")
            self.history = PlaylistHistory(
                playlist_id=None,  # We'll set this when we actually need it
                name=playlist_name,
                generations=[],
                current_generation=0,
            )
        else:
            logger.info(f"Loaded history with {len(self.history.generations)} generations")

    def _load_history(self) -> Optional[PlaylistHistory]:
        """Load playlist history from the SQLite rotation tables."""
        if self.repos is None:
            return None

        slug = _playlist_slug(self.playlist_name)
        row = self.repos.playlists.get(slug)
        if row is None:
            return None

        generations: List[List[str]] = []
        for gen in self.repos.rotation_generations.list_by_playlist(slug):
            tracks = self.repos.generation_tracks.list_by_generation(gen["generation_id"])
            generations.append([t["track_id"] for t in tracks])

        return PlaylistHistory(
            playlist_id=row["spotify_playlist_id"],
            name=row["name"],
            generations=generations,
            current_generation=row["current_generation"],
        )

    def _save_history(self):
        """Persist playlist history to the SQLite rotation tables."""
        if self.repos is None:
            return

        slug = _playlist_slug(self.playlist_name)
        now = datetime.now().isoformat()
        self.repos.playlists.upsert(
            playlist_id=slug,
            name=self.history.name,
            spotify_playlist_id=self.history.playlist_id,
            current_generation=self.history.current_generation,
            updated_at=now,
        )

        for gi, gen in enumerate(self.history.generations):
            generation_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}|{gi}").hex
            # upsert() is ON CONFLICT(playlist_id, generation_index) DO NOTHING and
            # returns the CANONICAL id — the existing row's id when one is already
            # present (which may differ from the id we just computed). Use that
            # returned id for generation_tracks, or we'd reference a non-existent
            # generation row and hit a FOREIGN KEY error.
            canonical_id = self.repos.rotation_generations.upsert(
                generation_id, slug, gi, created_at=now
            )
            for pos, sid in enumerate(gen):
                self.repos.generation_tracks.add(canonical_id, sid, pos)

        self.repos.conn.commit()

    def get_rotation_stats(self) -> RotationStats:
        """Get statistics about the playlist rotation"""
        all_used_songs = set()
        for gen_songs in self.history.generations:
            all_used_songs.update(gen_songs)

        total_songs = len(self.db.get_all_songs())
        unique_used = len(all_used_songs)

        return RotationStats(
            total_songs=total_songs,
            unique_songs_used=unique_used,
            generations_count=len(self.history.generations),
            songs_never_used=total_songs - unique_used,
            complete_rotation_achieved=unique_used == total_songs,
            current_strategy="similarity-based",
        )

    def _listen_signals(self) -> Tuple[Dict[str, datetime], Dict[str, float]]:
        """Real listen-ledger signals for selection (both empty when absent).

        Returns ``(track_id -> last real play as a naive local datetime,
        track_id -> recency-weighted play mass)`` from ``plays``. When the
        repos (and thus the listen ledger) are unavailable, both maps are
        empty and selection behaves exactly as before listen-awareness —
        the same holds per-track for tracks absent from the maps.
        """
        if self.repos is None:
            return {}, {}
        try:
            conn = self.repos.conn
            raw_last = last_played_map(conn)
            weights = recency_weights(conn)
        except Exception as e:
            logger.warning(f"Listen ledger unavailable, ignoring listen data: {e}")
            return {}, {}
        last: Dict[str, datetime] = {}
        for track_id, value in raw_last.items():
            parsed = parse_played_at(value)
            if parsed is None:
                continue
            # Selection compares against naive local datetime.now() dates;
            # convert the aware UTC timestamp to match.
            last[track_id] = parsed.astimezone().replace(tzinfo=None)
        return last, weights

    def _select_songs_with_history(
        self,
        history: PlaylistHistory,
        count: int = 10,
        fresh_days: int = 30,
        score_config: Optional[PlaylistScoreConfig] = None,
    ) -> List[Song]:
        """Select songs using a provided history snapshot."""
        today = datetime.now()
        all_songs = self.db.get_all_songs()
        if not all_songs:
            logger.warning("No songs available in the database for selection.")
            return []
        used_songs = history.all_used_songs

        # Real listen-ledger signals; tracks absent from these maps are
        # selected exactly as before (generation-index freshness only).
        last_played, listen_weights = self._listen_signals()
        with_data = sum(1 for s in all_songs if s.id in last_played)
        caption(f"{with_data} of {len(all_songs)} candidates had real listen data.")

        scores_by_id: Dict[str, float] = {}
        if score_config is not None:
            try:
                scorer = MatchScorer(
                    self.playlist_name, self.db, self.spotify, history, score_config
                )
                scores_by_id = scorer.score_candidates(all_songs)
            except Exception as e:
                logger.warning(f"Match scoring failed, falling back to legacy selection: {e}")
                scores_by_id = {}

        def rank_candidates(candidates: List[Song]) -> List[Song]:
            if scores_by_id:
                # Real listen mass folds into the match score as a demotion
                # penalty; tracks absent from the ledger keep their exact
                # pre-listen-aware ranking key.
                return sorted(
                    candidates,
                    key=lambda s: (
                        -(
                            scores_by_id.get(s.id, 0.0)
                            - LISTEN_DEMOTION_WEIGHT * listen_weights.get(s.id, 0.0)
                        ),
                        s.name,
                        s.artist,
                    ),
                )
            if listen_weights:
                # No match scores: stable sort by listen mass alone, so tracks
                # absent from the map (mass 0.0) keep their original order.
                return sorted(candidates, key=lambda s: listen_weights.get(s.id, 0.0))
            return candidates

        # First priority: songs that have never been used
        unused_songs = [s for s in all_songs if s.id not in used_songs]
        logger.info(f"Found {len(unused_songs)} songs that have never been used")

        if len(unused_songs) >= count:
            return rank_candidates(unused_songs)[:count]

        # Second priority: songs not used in the last fresh_days
        fresh_date_cutoff = today - timedelta(days=fresh_days)

        # Get songs used in each generation with timestamps
        recent_usage = {}
        for i, gen_songs in enumerate(history.generations):
            # Estimate the date based on generation index
            # Assuming one generation per day, counting backwards from today
            gen_date = today - timedelta(days=len(history.generations) - i)

            for song_id in gen_songs:
                # Keep the most recent usage date
                recent_usage[song_id] = gen_date

        # A real last-played timestamp trumps the one-generation-per-day
        # estimate whenever it is more recent: freshness uses the max of the
        # two, so a track actually heard yesterday never counts as fresh just
        # because its generation is old.
        for song_id, played_dt in last_played.items():
            estimate = recent_usage.get(song_id)
            if estimate is None or played_dt > estimate:
                recent_usage[song_id] = played_dt

        # Find songs not used in the fresh period
        fresh_songs = []
        for song in all_songs:
            if song.id in unused_songs:
                continue  # Already counted in unused_songs

            if song.id not in recent_usage:
                # This shouldn't happen, but just in case
                fresh_songs.append(song)
                continue

            last_used = recent_usage[song.id]
            if last_used < fresh_date_cutoff:
                fresh_songs.append(song)

        logger.info(
            f"Found {len(fresh_songs)} additional songs not used in the last {fresh_days} days"
        )

        # Combine unused and fresh songs
        selected = rank_candidates(unused_songs) + rank_candidates(fresh_songs)
        if len(selected) >= count:
            return selected[:count]

        # Third priority: use scoring or similarity-based selection for remaining slots
        remaining_count = count - len(selected)
        logger.info(
            f"Need {remaining_count} more songs, using match scoring or similarity fallback"
        )

        # Exclude already selected songs from the remaining candidate pool
        selected_ids = {s.id for s in selected}
        candidates = [s for s in all_songs if s.id not in selected_ids]

        if scores_by_id:
            ranked_remaining = rank_candidates(candidates)
            return selected + ranked_remaining[:remaining_count]

        # Fallback to legacy similarity search
        if not selected and not all_songs:
            return []
        seed_song = selected[-1] if selected else all_songs[0]
        similar_songs = self.db.find_similar_songs(seed_song, k=remaining_count, threshold=0.7)

        # If we still don't have enough songs, add random ones from the remaining pool
        if len(selected) + len(similar_songs) < count and candidates:
            remaining_needed = count - (len(selected) + len(similar_songs))
            logger.info(f"Still need {remaining_needed} more songs, adding random selections")

            # Shuffle the candidates to get random selections
            random_candidates = list(candidates)
            random.shuffle(random_candidates)

            # Add random songs, avoiding any that are already in similar_songs
            similar_song_ids = {s.id for s in similar_songs}
            random_selections = []
            for song in random_candidates:
                if song.id not in similar_song_ids and len(random_selections) < remaining_needed:
                    random_selections.append(song)

            # Combine all selections
            return selected + similar_songs + random_selections

        return selected + similar_songs[:remaining_count]

    def select_songs_for_today(
        self,
        count: int = 10,
        fresh_days: int = 30,
        score_config: Optional[PlaylistScoreConfig] = None,
    ) -> List[Song]:
        """Select songs for today's playlist, prioritizing songs not listened to recently

        Args:
            count: Number of songs to select
            fresh_days: Prioritize songs not used in this many days
        """
        return self._select_songs_with_history(
            self.history, count=count, fresh_days=fresh_days, score_config=score_config
        )

    def simulate_generations(
        self,
        count: int = 10,
        fresh_days: int = 30,
        generations: int = 3,
        score_config: Optional[PlaylistScoreConfig] = None,
    ) -> List[List[Song]]:
        """Simulate future generations without writing history to disk."""
        import copy

        simulated_history = copy.deepcopy(self.history)
        plans: List[List[Song]] = []

        for _ in range(max(0, generations)):
            songs = self._select_songs_with_history(
                simulated_history, count=count, fresh_days=fresh_days, score_config=score_config
            )
            plans.append(songs)
            simulated_history.generations.append([song.id for song in songs])
            simulated_history.current_generation += 1

        return plans

    def update_playlist(self, songs: List[Song], record_generation: bool = True) -> bool:
        """Update the playlist with the given songs by deleting and recreating it"""
        try:
            # Get or create playlist
            logger.info(f"Refreshing playlist '{self.playlist_name}' with {len(songs)} songs...")

            # Verify we have valid songs before updating
            valid_songs = [
                song for song in songs if song.spotify_uri or self.spotify.search_song(song)
            ]

            if not valid_songs and songs:
                logger.warning("No valid songs found with Spotify URIs. Will use fallback songs.")

            # Use the spotify manager instance to update the playlist
            logger.info(f"Updating playlist '{self.playlist_name}' with songs:")
            for i, song in enumerate(songs, 1):
                logger.info(f"  {i}. {song.name} by {song.artist}")

            # Force delete and recreate the playlist
            success = self.spotify.refresh_playlist(self.playlist_name, songs)

            if not success:
                logger.error(f"Failed to update playlist '{self.playlist_name}'")
                return False

            # Persist any URI changes discovered during Spotify search
            self.db._save_state()

            # Update history even if we used fallback songs
            logger.info("Updating playlist history...")
            if record_generation:
                self.history.generations.append([song.id for song in songs])
                self.history.current_generation += 1
            self._save_history()

            logger.info(f"Successfully updated playlist '{self.playlist_name}'")
            return True

        except Exception as e:
            logger.error(f"Error updating playlist: {str(e)}")
            logger.debug("Full error:", exc_info=True)
            return False

    def get_recent_generations(self, count: int = 5) -> List[List[Song]]:
        """Get the most recent generations of songs"""
        recent_gens = []
        for gen_songs in self.history.generations[-count:]:
            songs = [self.db.get_song_by_id(sid) for sid in gen_songs]
            recent_gens.append([s for s in songs if s is not None])
        return recent_gens

    def get_recent_songs(self, days: int = 7) -> Dict[str, List[Song]]:
        """Get songs used in the last N days, grouped by date"""
        from datetime import datetime, timedelta

        today = datetime.now()

        # Create a dictionary to store songs by date
        songs_by_date = {}

        # Get the most recent generations
        recent_count = min(days, len(self.history.generations))
        if recent_count == 0:
            return {}

        recent_gens = self.history.generations[-recent_count:]

        # Assign dates to generations (estimate based on current date)
        for i, gen_songs in enumerate(recent_gens):
            # Estimate date: today - (number of days from most recent)
            gen_date = today - timedelta(days=recent_count - i - 1)
            date_str = gen_date.strftime("%Y-%m-%d")

            # Get song objects
            songs = []
            for sid in gen_songs:
                song = self.db.get_song_by_id(sid)
                if song:
                    songs.append(song)

            songs_by_date[date_str] = songs

        return songs_by_date
