import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Callable
from pathlib import Path
import json

from models import Song, PlaylistHistory, RotationStats
from db_manager import DatabaseManager
from spotify_manager import SpotifyManager

logger = logging.getLogger(__name__)

class RotationManager:
    """Manages the rotation of songs in playlists"""
    
    def __init__(self, playlist_name: str, history_dir: str = "data/history"):
        self.playlist_name = playlist_name
        self.history_dir = Path(history_dir)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        
        self.db = DatabaseManager()
        self.spotify = SpotifyManager()
        self.history = self._load_history()

    def _load_history(self) -> PlaylistHistory:
        """Load playlist history from disk"""
        history_file = self.history_dir / f"{self.playlist_name}_history.json"
        if history_file.exists():
            with open(history_file, 'r') as f:
                data = json.load(f)
                return PlaylistHistory(
                    playlist_id=data['playlist_id'],
                    name=data['name'],
                    generations=data.get('generations', []),
                    current_generation=data.get('current_generation', 0)
                )
        
        # Create new playlist if doesn't exist
        playlist_id = self.spotify.create_playlist(self.playlist_name)
        return PlaylistHistory(
            playlist_id=playlist_id,
            name=self.playlist_name,
            generations=[],
            current_generation=0
        )

    def _save_history(self):
        """Save playlist history to disk"""
        history_file = self.history_dir / f"{self.playlist_name}_history.json"
        with open(history_file, 'w') as f:
            json.dump({
                'playlist_id': self.history.playlist_id,
                'name': self.history.name,
                'generations': self.history.generations,
                'current_generation': self.history.current_generation
            }, f, indent=2)

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

    def update_playlist(self, songs: Optional[List[Song]] = None, progress_callback: Optional[callable] = None):
        """Update the playlist with new songs"""
        if songs is None:
            songs = self.select_songs_for_today()
        
        # Update Spotify playlist
        success = self.spotify.refresh_playlist(
            self.playlist_name, 
            songs,
            progress_callback=progress_callback
        )
        
        if success:
            # Update history with new generation
            self.history.generations.append([s.id for s in songs])
            self.history.current_generation += 1
            self._save_history()
            logger.info(f"Successfully updated playlist with {len(songs)} songs (Generation {self.history.current_generation})")
        else:
            logger.error("Failed to update Spotify playlist")

    def get_recent_generations(self, count: int = 5) -> List[List[Song]]:
        """Get the most recent generations of songs"""
        recent_gens = []
        for gen_songs in self.history.generations[-count:]:
            songs = [self.db.get_song_by_id(sid) for sid in gen_songs]
            recent_gens.append([s for s in songs if s is not None])
        return recent_gens 