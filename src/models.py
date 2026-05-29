from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Set


def track_id_for(artist: str, name: str) -> str:
    """Canonical track-id derived from an artist/title pair.

    Matches the historical convention ``f"{artist.lower()}|||{name.lower()}"``
    used as both ``Song.id`` and the SQLite ``tracks.track_id`` primary key.
    """
    return f"{artist.lower()}|||{name.lower()}"


@dataclass
class Song:
    """Represents a song with its metadata"""

    id: str  # Unique identifier (artist_name|||song_name)
    name: str
    artist: str
    embedding: Optional[List[float]] = None
    spotify_uri: Optional[str] = None
    first_added: Optional[datetime] = None


@dataclass
class PlaylistHistory:
    """Represents the history of a playlist"""

    playlist_id: str
    name: str
    generations: List[List[str]]  # List of generations, each containing song IDs
    current_generation: int = 0

    @property
    def all_used_songs(self) -> Set[str]:
        used = set()
        for gen_songs in self.generations:
            used.update(gen_songs)
        return used


@dataclass
class RotationStats:
    """Statistics about playlist rotation"""

    total_songs: int
    unique_songs_used: int
    generations_count: int  # Changed from days_of_history
    songs_never_used: int
    complete_rotation_achieved: bool
    current_strategy: str
