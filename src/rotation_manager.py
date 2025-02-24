import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Callable
from pathlib import Path
import json
import pickle

from models import Song, PlaylistHistory, RotationStats
from db_manager import DatabaseManager
from spotify_manager import SpotifyManager

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

    def select_songs_for_today(self, count: int = 30) -> List[Song]:
        """Select songs for today's playlist"""
        today = datetime.now().strftime("%Y-%m-%d")
        all_songs = self.db.get_all_songs()
        used_songs = self.history.all_used_songs
        
        # First, try to use songs that haven't been used yet
        unused_songs = [s for s in all_songs if s.id not in used_songs]
        if unused_songs:
            selected = unused_songs[:count]
            if len(selected) >= count:
                return selected

        # If we need more songs, use similarity-based selection
        remaining_count = count - len(unused_songs)
        used_songs_list = [s for s in all_songs if s.id in used_songs]
        
        # Use the last song from unused as seed for similarity search
        seed_song = unused_songs[-1] if unused_songs else used_songs_list[0]
        similar_songs = self.db.find_similar_songs(seed_song, k=remaining_count, threshold=0.7)
        
        return unused_songs + similar_songs[:remaining_count]

    def update_playlist(self, songs: List[Song]) -> bool:
        """Update the playlist with the given songs"""
        try:
            # Get or create playlist
            if not self.spotify.refresh_playlist(self.playlist_name, songs):
                logger.error(f"Failed to update playlist '{self.playlist_name}'")
                return False
            
            # Update history
            self.history.generations.append([song.id for song in songs])
            self.history.current_generation += 1
            self._save_history()
            
            return True
            
        except Exception as e:
            logger.error(f"Error updating playlist: {str(e)}")
            return False

    def get_recent_generations(self, count: int = 5) -> List[List[Song]]:
        """Get the most recent generations of songs"""
        recent_gens = []
        for gen_songs in self.history.generations[-count:]:
            songs = [self.db.get_song_by_id(sid) for sid in gen_songs]
            recent_gens.append([s for s in songs if s is not None])
        return recent_gens 