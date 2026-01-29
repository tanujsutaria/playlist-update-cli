"""
Shared pytest fixtures for all tests.
Provides mocked managers and sample data to enable testing without Spotify credentials.
"""
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch
from typing import List, Dict

import numpy as np
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from models import Song, PlaylistHistory, RotationStats  # noqa: E402


# =============================================================================
# Sample Data Fixtures
# =============================================================================

@pytest.fixture
def sample_songs() -> List[Song]:
    """Create a list of test Song objects"""
    return [
        Song(
            id="artist1|||song1",
            name="song1",
            artist="artist1",
            embedding=np.random.rand(384).tolist(),
            spotify_uri="spotify:track:abc123",
            first_added=datetime(2024, 1, 1)
        ),
        Song(
            id="artist2|||song2",
            name="song2",
            artist="artist2",
            embedding=np.random.rand(384).tolist(),
            spotify_uri="spotify:track:def456",
            first_added=datetime(2024, 1, 2)
        ),
        Song(
            id="artist3|||song3",
            name="song3",
            artist="artist3",
            embedding=np.random.rand(384).tolist(),
            spotify_uri="spotify:track:ghi789",
            first_added=datetime(2024, 1, 3)
        ),
        Song(
            id="artist4|||song4",
            name="song4",
            artist="artist4",
            embedding=np.random.rand(384).tolist(),
            spotify_uri="spotify:track:jkl012",
            first_added=datetime(2024, 1, 4)
        ),
        Song(
            id="artist5|||song5",
            name="song5",
            artist="artist5",
            embedding=np.random.rand(384).tolist(),
            spotify_uri="spotify:track:mno345",
            first_added=datetime(2024, 1, 5)
        ),
    ]


@pytest.fixture
def sample_song() -> Song:
    """Create a single test Song object"""
    return Song(
        id="testartist|||testsong",
        name="testsong",
        artist="testartist",
        embedding=np.random.rand(384).tolist(),
        spotify_uri="spotify:track:test123",
        first_added=datetime(2024, 1, 1)
    )


@pytest.fixture
def sample_playlist_history() -> PlaylistHistory:
    """Create a test PlaylistHistory object"""
    return PlaylistHistory(
        playlist_id="test_playlist_id",
        name="Test Playlist",
        generations=[
            ["artist1|||song1", "artist2|||song2"],
            ["artist3|||song3", "artist4|||song4"],
            ["artist5|||song5", "artist1|||song1"],
        ],
        current_generation=2
    )


@pytest.fixture
def sample_rotation_stats() -> RotationStats:
    """Create a test RotationStats object"""
    return RotationStats(
        total_songs=100,
        unique_songs_used=50,
        generations_count=10,
        songs_never_used=50,
        complete_rotation_achieved=False,
        current_strategy="similarity-based"
    )


# =============================================================================
# Mock Spotify Fixtures
# =============================================================================

@pytest.fixture
def mock_spotipy():
    """Create a mocked spotipy.Spotify client"""
    mock_sp = MagicMock()

    # Mock current_user
    mock_sp.current_user.return_value = {'id': 'test_user_id'}

    # Mock current_user_playlists
    mock_sp.current_user_playlists.return_value = {
        'items': [
            {'name': 'Test Playlist', 'id': 'playlist_123', 'owner': {'id': 'test_user_id'}},
            {'name': 'Another Playlist', 'id': 'playlist_456', 'owner': {'id': 'test_user_id'}},
        ]
    }

    # Mock search
    def mock_search(query, type='track', limit=10):
        return {
            'tracks': {
                'items': [
                    {
                        'name': 'Test Track',
                        'uri': 'spotify:track:mock123',
                        'artists': [{'id': 'artist_id', 'name': 'Test Artist'}]
                    }
                ]
            }
        }
    mock_sp.search.side_effect = mock_search

    # Mock playlist_tracks
    mock_sp.playlist_tracks.return_value = {
        'items': [
            {
                'added_at': '2024-01-01T00:00:00Z',
                'track': {
                    'name': 'Track 1',
                    'uri': 'spotify:track:track1',
                    'artists': [{'name': 'Artist 1'}]
                }
            },
            {
                'added_at': '2024-01-02T00:00:00Z',
                'track': {
                    'name': 'Track 2',
                    'uri': 'spotify:track:track2',
                    'artists': [{'name': 'Artist 2'}]
                }
            }
        ],
        'next': None
    }

    # Mock user_playlist_create
    mock_sp.user_playlist_create.return_value = {'id': 'new_playlist_id'}

    # Mock playlist_add_items
    mock_sp.playlist_add_items.return_value = None

    # Mock current_user_unfollow_playlist
    mock_sp.current_user_unfollow_playlist.return_value = None

    # Mock artist
    mock_sp.artist.return_value = {
        'id': 'artist_id',
        'name': 'Test Artist',
        'followers': {'total': 500000}
    }

    # Mock track
    mock_sp.track.return_value = {
        'name': 'Test Track',
        'uri': 'spotify:track:mock123',
        'artists': [{'id': 'artist_id', 'name': 'Test Artist'}]
    }

    return mock_sp


@pytest.fixture
def mock_spotify_manager(mock_spotipy):
    """Create a fully mocked SpotifyManager for CLI tests"""
    # Create a MagicMock for the manager itself
    manager = MagicMock()
    manager.sp = mock_spotipy
    manager.user_id = 'test_user_id'
    manager.playlists = {
        'Test Playlist': 'playlist_123',
        'Another Playlist': 'playlist_456'
    }
    manager.cache_dir = Path('/tmp/test_spotify_cache')
    manager.cache_path = manager.cache_dir / '.spotify_token'

    # Mock manager methods
    manager.search_song.return_value = 'spotify:track:mock123'
    manager.create_playlist.return_value = 'new_playlist_id'
    manager.refresh_playlist.return_value = True
    manager.append_to_playlist.return_value = True
    manager.remove_from_playlist.return_value = True
    manager.get_playlist_tracks.return_value = [
        {'name': 'Track 1', 'artist': 'Artist 1', 'uri': 'spotify:track:track1', 'added_at': '2024-01-01T00:00:00Z'},
        {'name': 'Track 2', 'artist': 'Artist 2', 'uri': 'spotify:track:track2', 'added_at': '2024-01-02T00:00:00Z'},
    ]
    manager.get_track_info.return_value = {
        'name': 'Test Track',
        'artist': 'Test Artist',
        'uri': 'spotify:track:mock123'
    }
    manager.get_playlist_id.side_effect = lambda name: manager.playlists.get(name)

    return manager


@pytest.fixture
def real_spotify_manager_with_mock_client(mock_spotipy):
    """Create a real SpotifyManager with mocked spotipy client for testing actual methods"""
    with patch('spotify_manager.spotipy.Spotify', return_value=mock_spotipy):
        with patch('spotify_manager.SpotifyOAuth'):
            with patch.dict('os.environ', {
                'SPOTIFY_CLIENT_ID': 'test_client_id',
                'SPOTIFY_CLIENT_SECRET': 'test_client_secret',
                'SPOTIFY_REDIRECT_URI': 'http://localhost:8888/callback'
            }):
                from spotify_manager import SpotifyManager

                # Create instance without triggering real __init__
                manager = SpotifyManager.__new__(SpotifyManager)
                manager.sp = mock_spotipy
                manager.user_id = 'test_user_id'
                manager.playlists = {
                    'Test Playlist': 'playlist_123',
                    'Another Playlist': 'playlist_456'
                }
                manager.cache_dir = Path('/tmp/test_spotify_cache')
                manager.cache_path = manager.cache_dir / '.spotify_token'

                yield manager


# =============================================================================
# Mock Database Fixtures
# =============================================================================

@pytest.fixture
def mock_database_manager(tmp_path, sample_songs):
    """Create a mocked DatabaseManager using temp directory"""
    # Create a MagicMock instead of real DatabaseManager
    db = MagicMock()

    # Set up paths
    db.data_dir = tmp_path / "data"
    db.embeddings_dir = tmp_path / "data" / "embeddings"
    db.embeddings_dir.mkdir(parents=True, exist_ok=True)

    # Set up songs dictionary for internal use
    songs_dict = {song.id: song for song in sample_songs}

    # Mock methods
    db.get_all_songs.return_value = sample_songs
    db.get_song_by_id.side_effect = lambda sid: songs_dict.get(sid)
    db.add_song.return_value = True
    db.remove_song.return_value = True
    db.find_similar_songs.return_value = sample_songs[:2]
    db.get_stats.return_value = {
        'total_songs': len(sample_songs),
        'embedding_dimensions': 384,
        'storage_size_mb': 1.5
    }

    # Set up embeddings array
    embeddings_list = []
    for song in sample_songs:
        if song.embedding:
            embeddings_list.append(song.embedding)
        else:
            embeddings_list.append(np.zeros(384))
    db.embeddings = np.array(embeddings_list)
    db.songs = songs_dict

    return db


@pytest.fixture
def empty_database_manager(tmp_path):
    """Create a mocked DatabaseManager with no songs"""
    db = MagicMock()

    db.data_dir = tmp_path / "data"
    db.embeddings_dir = tmp_path / "data" / "embeddings"
    db.embeddings_dir.mkdir(parents=True, exist_ok=True)

    # Mock methods for empty database
    db.get_all_songs.return_value = []
    db.get_song_by_id.return_value = None
    db.add_song.return_value = True
    db.remove_song.return_value = False
    db.find_similar_songs.return_value = []
    db.get_stats.return_value = {
        'total_songs': 0,
        'embedding_dimensions': 0,
        'storage_size_mb': 0
    }
    db.songs = {}
    db.embeddings = np.array([])

    return db


# =============================================================================
# Mock Rotation Manager Fixtures
# =============================================================================

@pytest.fixture
def mock_rotation_manager(mock_database_manager, mock_spotify_manager, sample_playlist_history, sample_songs, tmp_path):
    """Create a mocked RotationManager"""
    from rotation_manager import RotationManager

    # Create instance without triggering real __init__
    rm = RotationManager.__new__(RotationManager)
    rm.playlist_name = "Test Playlist"
    rm.db = mock_database_manager
    rm.spotify = mock_spotify_manager
    rm.history = sample_playlist_history
    rm.root_dir = tmp_path
    rm.history_dir = tmp_path / "history"
    rm.history_dir.mkdir(parents=True, exist_ok=True)
    rm._save_history = MagicMock()

    # Make sure db.get_all_songs returns sample_songs for the rotation manager
    mock_database_manager.get_all_songs.return_value = sample_songs

    return rm


# =============================================================================
# Mock CLI Fixtures
# =============================================================================

@pytest.fixture
def mock_cli(mock_database_manager, mock_spotify_manager):
    """Create a PlaylistCLI instance with mocked managers injected"""
    from main import PlaylistCLI

    # Create instance without triggering real __init__
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._db = mock_database_manager
    cli._spotify = mock_spotify_manager
    cli._rotation_managers = {}

    return cli


@pytest.fixture
def cli_no_init():
    """Create a PlaylistCLI instance without any initialization"""
    from main import PlaylistCLI

    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._db = None
    cli._spotify = None
    cli._rotation_managers = {}

    return cli


# =============================================================================
# File System Fixtures
# =============================================================================

@pytest.fixture
def csv_file(tmp_path):
    """Create a test CSV file with song data"""
    csv = tmp_path / "test_songs.csv"
    csv.write_text("song1,artist1\nsong2,artist2\nsong3,artist3\n")
    return csv


@pytest.fixture
def csv_file_with_comments(tmp_path):
    """Create a test CSV file with comments and empty lines"""
    csv = tmp_path / "test_songs.csv"
    csv.write_text("# This is a comment\nsong1,artist1\n\nsong2,artist2\n# Another comment\n")
    return csv


@pytest.fixture
def backup_dir(tmp_path):
    """Create a test backup directory with sample backups"""
    backups = tmp_path / "backups"
    backups.mkdir()

    # Create sample backup folders
    backup1 = backups / "20240101_120000"
    backup1.mkdir()
    (backup1 / "songs.pkl").write_bytes(b"x" * 1024)

    backup2 = backups / "my_backup"
    backup2.mkdir()
    (backup2 / "songs.pkl").write_bytes(b"x" * 2048)
    (backup2 / "embeddings.npy").write_bytes(b"x" * 4096)

    return backups


@pytest.fixture
def data_dir(tmp_path):
    """Create a test data directory with sample files"""
    data = tmp_path / "data"
    data.mkdir()

    embeddings = data / "embeddings"
    embeddings.mkdir()
    (embeddings / "songs.pkl").write_bytes(b"x" * 1024)
    (embeddings / "embeddings.npy").write_bytes(b"x" * 2048)

    history = data / "history"
    history.mkdir()

    return data
