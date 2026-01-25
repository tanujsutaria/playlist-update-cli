"""
Mock response factories and helper classes for testing.
Provides functions to create realistic Spotify API responses and mock objects.
"""
from typing import List, Dict, Optional
from unittest.mock import MagicMock
from datetime import datetime


# =============================================================================
# Spotify API Response Factories
# =============================================================================

def create_spotify_track_response(
    name: str,
    artist: str,
    uri: str = None,
    artist_id: str = None
) -> Dict:
    """Create a mock Spotify track object"""
    if uri is None:
        uri = f"spotify:track:{name.lower().replace(' ', '')}"
    if artist_id is None:
        artist_id = f"artist_{artist.lower().replace(' ', '')}"

    return {
        'name': name,
        'uri': uri,
        'id': uri.split(':')[-1],
        'artists': [
            {
                'id': artist_id,
                'name': artist
            }
        ],
        'album': {
            'name': f"{name} Album",
            'id': f"album_{name.lower().replace(' ', '')}"
        },
        'duration_ms': 180000,
        'popularity': 50
    }


def create_spotify_search_response(tracks: List[Dict] = None) -> Dict:
    """Create a mock Spotify search API response"""
    if tracks is None:
        tracks = [create_spotify_track_response("Default Track", "Default Artist")]

    return {
        'tracks': {
            'items': tracks,
            'total': len(tracks),
            'limit': 10,
            'offset': 0
        }
    }


def create_spotify_playlist_response(
    name: str,
    playlist_id: str,
    owner_id: str = "test_user_id"
) -> Dict:
    """Create a mock playlist object"""
    return {
        'id': playlist_id,
        'name': name,
        'owner': {'id': owner_id},
        'public': False,
        'tracks': {'total': 0},
        'description': f"Test playlist: {name}"
    }


def create_spotify_artist_response(
    name: str,
    followers: int = 500000,
    artist_id: str = None
) -> Dict:
    """Create a mock artist object with configurable follower count"""
    if artist_id is None:
        artist_id = f"artist_{name.lower().replace(' ', '')}"

    return {
        'id': artist_id,
        'name': name,
        'followers': {'total': followers},
        'popularity': 50,
        'genres': ['rock', 'pop']
    }


def create_spotify_playlist_tracks_response(
    tracks: List[Dict] = None,
    has_next: bool = False
) -> Dict:
    """Create a mock playlist_tracks API response"""
    if tracks is None:
        tracks = []

    items = []
    for i, track in enumerate(tracks):
        items.append({
            'added_at': f"2024-01-{(i+1):02d}T00:00:00Z",
            'track': track
        })

    return {
        'items': items,
        'total': len(items),
        'next': 'http://next_page' if has_next else None
    }


def create_spotify_user_playlists_response(playlists: List[Dict] = None) -> Dict:
    """Create a mock current_user_playlists API response"""
    if playlists is None:
        playlists = []

    return {
        'items': playlists,
        'total': len(playlists)
    }


# =============================================================================
# Mock Spotipy Client Class
# =============================================================================

class MockSpotipyClient:
    """
    Drop-in replacement for spotipy.Spotify that returns configurable mock data.
    Use this for more complex test scenarios where you need fine-grained control.
    """

    def __init__(self):
        self.user_id = "test_user_id"
        self._playlists = {}
        self._tracks = {}
        self._artists = {}
        self._search_results = {}

    def current_user(self) -> Dict:
        return {'id': self.user_id}

    def current_user_playlists(self) -> Dict:
        items = [
            create_spotify_playlist_response(name, pid, self.user_id)
            for name, pid in self._playlists.items()
        ]
        return create_spotify_user_playlists_response(items)

    def user_playlist_create(self, user_id: str, name: str, public: bool = False, description: str = "") -> Dict:
        playlist_id = f"new_playlist_{len(self._playlists)}"
        self._playlists[name] = playlist_id
        return create_spotify_playlist_response(name, playlist_id, user_id)

    def search(self, query: str, type: str = 'track', limit: int = 10) -> Dict:
        # Check for configured search results first
        if query in self._search_results:
            return self._search_results[query]

        # Return default empty result
        return create_spotify_search_response([])

    def playlist_tracks(self, playlist_id: str, fields: str = None) -> Dict:
        tracks = self._tracks.get(playlist_id, [])
        return create_spotify_playlist_tracks_response(tracks)

    def playlist_add_items(self, playlist_id: str, items: List[str]) -> None:
        if playlist_id not in self._tracks:
            self._tracks[playlist_id] = []
        # Add track URIs as track objects
        for uri in items:
            self._tracks[playlist_id].append(
                create_spotify_track_response(f"Track {len(self._tracks[playlist_id])}", "Artist", uri)
            )

    def playlist_remove_all_occurrences_of_items(self, playlist_id: str, items: List[str]) -> None:
        if playlist_id in self._tracks:
            self._tracks[playlist_id] = [
                t for t in self._tracks[playlist_id]
                if t['uri'] not in items
            ]

    def current_user_unfollow_playlist(self, playlist_id: str) -> None:
        # Find and remove playlist by ID
        name_to_remove = None
        for name, pid in self._playlists.items():
            if pid == playlist_id:
                name_to_remove = name
                break
        if name_to_remove:
            del self._playlists[name_to_remove]
            if playlist_id in self._tracks:
                del self._tracks[playlist_id]

    def artist(self, artist_id: str) -> Dict:
        if artist_id in self._artists:
            return self._artists[artist_id]
        return create_spotify_artist_response("Unknown Artist", 100000, artist_id)

    def track(self, track_uri: str) -> Dict:
        return create_spotify_track_response("Test Track", "Test Artist", track_uri)

    def next(self, result: Dict) -> Optional[Dict]:
        # For pagination - return None to indicate no more pages
        return None

    # Configuration methods for tests
    def add_playlist(self, name: str, playlist_id: str) -> None:
        """Add a playlist to the mock"""
        self._playlists[name] = playlist_id

    def add_artist(self, artist_id: str, name: str, followers: int) -> None:
        """Add an artist with specific follower count"""
        self._artists[artist_id] = create_spotify_artist_response(name, followers, artist_id)

    def set_search_result(self, query: str, tracks: List[Dict]) -> None:
        """Configure search results for a specific query"""
        self._search_results[query] = create_spotify_search_response(tracks)

    def add_tracks_to_playlist(self, playlist_id: str, tracks: List[Dict]) -> None:
        """Add tracks to a playlist for playlist_tracks calls"""
        self._tracks[playlist_id] = tracks


# =============================================================================
# Test Data Generators
# =============================================================================

def generate_song_batch(count: int = 10, start_index: int = 0) -> List[Dict]:
    """Generate a batch of test songs"""
    from models import Song
    import numpy as np

    songs = []
    for i in range(start_index, start_index + count):
        songs.append(Song(
            id=f"artist{i}|||song{i}",
            name=f"song{i}",
            artist=f"artist{i}",
            embedding=np.random.rand(384).tolist(),
            spotify_uri=f"spotify:track:track{i}",
            first_added=datetime(2024, 1, (i % 28) + 1)
        ))
    return songs


def generate_playlist_history(
    playlist_name: str,
    generations_count: int = 5,
    songs_per_generation: int = 10
) -> 'PlaylistHistory':
    """Generate a test PlaylistHistory with multiple generations"""
    from models import PlaylistHistory

    generations = []
    for gen in range(generations_count):
        song_ids = [
            f"artist{(gen * songs_per_generation) + i}|||song{(gen * songs_per_generation) + i}"
            for i in range(songs_per_generation)
        ]
        generations.append(song_ids)

    return PlaylistHistory(
        playlist_id=f"playlist_{playlist_name.lower().replace(' ', '_')}",
        name=playlist_name,
        generations=generations,
        current_generation=generations_count - 1
    )
