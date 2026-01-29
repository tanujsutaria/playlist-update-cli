import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Callable
from pathlib import Path
import json
import pickle

from models import Song, PlaylistHistory, RotationStats
from db_manager import DatabaseManager
from spotify_manager import SpotifyManager
from scoring import ScoreConfig, MatchScorer

logger = logging.getLogger(__name__)

class RotationManager:
    """Manages the rotation of songs in playlists"""
    
    def __init__(self, playlist_name: str, db: DatabaseManager, spotify: SpotifyManager):
        self.playlist_name = playlist_name
        self.db = db
        self.spotify = spotify
        
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
                current_generation=0
            )
        else:
            logger.info(f"Loaded history with {len(self.history.generations)} generations")

    def _load_history(self) -> Optional[PlaylistHistory]:
        """Load playlist history from disk"""
        history_dir = self.root_dir / "data/history"
        history_dir.mkdir(parents=True, exist_ok=True)
        
        history_file = history_dir / f"{self.playlist_name.lower().replace(' ', '_')}.pkl"
        logger.debug(f"Loading history from: {history_file}")
        
        if history_file.exists():
            try:
                with open(history_file, 'rb') as f:
                    history = pickle.load(f)
                    logger.info(f"Loaded history with {len(history.generations)} generations")
                    return history
            except Exception as e:
                logger.error(f"Error loading history: {str(e)}")
        return None

    def _save_history(self):
        """Save playlist history to disk"""
        history_dir = self.root_dir / "data/history"
        history_file = history_dir / f"{self.playlist_name.lower().replace(' ', '_')}.pkl"
        
        try:
            with open(history_file, 'wb') as f:
                pickle.dump(self.history, f)
                logger.debug(f"Saved history to: {history_file}")
        except Exception as e:
            logger.error(f"Error saving history: {str(e)}")

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
            current_strategy="similarity-based"
        )

    def _select_songs_with_history(
        self,
        history: PlaylistHistory,
        count: int = 10,
        fresh_days: int = 30,
        score_config: Optional[ScoreConfig] = None
    ) -> List[Song]:
        """Select songs using a provided history snapshot."""
        today = datetime.now()
        all_songs = self.db.get_all_songs()
        used_songs = history.all_used_songs

        scores_by_id: Dict[str, float] = {}
        if score_config is not None:
            try:
                scorer = MatchScorer(self.playlist_name, self.db, self.spotify, history, score_config)
                scores_by_id = scorer.score_candidates(all_songs)
            except Exception as e:
                logger.warning(f"Match scoring failed, falling back to legacy selection: {e}")
                scores_by_id = {}

        def rank_candidates(candidates: List[Song]) -> List[Song]:
            if not scores_by_id:
                return candidates
            return sorted(
                candidates,
                key=lambda s: (-scores_by_id.get(s.id, 0.0), s.name, s.artist)
            )
        
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
        
        logger.info(f"Found {len(fresh_songs)} additional songs not used in the last {fresh_days} days")
        
        # Combine unused and fresh songs
        selected = rank_candidates(unused_songs) + rank_candidates(fresh_songs)
        if len(selected) >= count:
            return selected[:count]
        
        # Third priority: use scoring or similarity-based selection for remaining slots
        remaining_count = count - len(selected)
        logger.info(f"Need {remaining_count} more songs, using match scoring or similarity fallback")

        # Exclude already selected songs from the remaining candidate pool
        selected_ids = {s.id for s in selected}
        candidates = [s for s in all_songs if s.id not in selected_ids]

        if scores_by_id:
            ranked_remaining = rank_candidates(candidates)
            return selected + ranked_remaining[:remaining_count]

        # Fallback to legacy similarity search
        seed_song = selected[-1] if selected else all_songs[0]
        similar_songs = self.db.find_similar_songs(seed_song, k=remaining_count, threshold=0.7)
        
        # If we still don't have enough songs, add random ones from the remaining pool
        if len(selected) + len(similar_songs) < count and candidates:
            import random
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
        score_config: Optional[ScoreConfig] = None
    ) -> List[Song]:
        """Select songs for today's playlist, prioritizing songs not listened to recently
        
        Args:
            count: Number of songs to select
            fresh_days: Prioritize songs not used in this many days
        """
        return self._select_songs_with_history(
            self.history,
            count=count,
            fresh_days=fresh_days,
            score_config=score_config
        )

    def simulate_generations(
        self,
        count: int = 10,
        fresh_days: int = 30,
        generations: int = 3,
        score_config: Optional[ScoreConfig] = None
    ) -> List[List[Song]]:
        """Simulate future generations without writing history to disk."""
        import copy
        simulated_history = copy.deepcopy(self.history)
        plans: List[List[Song]] = []

        for _ in range(max(0, generations)):
            songs = self._select_songs_with_history(
                simulated_history,
                count=count,
                fresh_days=fresh_days,
                score_config=score_config
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
            valid_songs = [song for song in songs if song.spotify_uri or self.spotify.search_song(song)]
            
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
        
        # Calculate date range
        today = datetime.now()
        start_date = today - timedelta(days=days)
        
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
            gen_date = today - timedelta(days=recent_count-i-1)
            date_str = gen_date.strftime("%Y-%m-%d")
            
            # Get song objects
            songs = []
            for sid in gen_songs:
                song = self.db.get_song_by_id(sid)
                if song:
                    songs.append(song)
            
            songs_by_date[date_str] = songs
            
        return songs_by_date
