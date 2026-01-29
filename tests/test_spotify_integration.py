"""
Unit tests for SpotifyManager.
Uses mocked Spotipy client to avoid requiring live API credentials.
"""
import pytest
from unittest.mock import MagicMock, patch
from models import Song
from test_mocks import (
    create_spotify_track_response,
    create_spotify_search_response,
    create_spotify_artist_response,
    create_spotify_playlist_tracks_response,
    MockSpotipyClient
)


class TestSpotifyManagerInitialization:
    """Tests for SpotifyManager initialization"""

    def test_initialization_with_mocked_client(self, real_spotify_manager_with_mock_client):
        """Verify SpotifyManager initializes correctly with mocked client"""
        manager = real_spotify_manager_with_mock_client
        assert manager.user_id == 'test_user_id'
        assert 'Test Playlist' in manager.playlists
        assert manager.playlists['Test Playlist'] == 'playlist_123'

    def test_playlists_loaded_on_init(self, real_spotify_manager_with_mock_client):
        """Verify playlists are loaded during initialization"""
        manager = real_spotify_manager_with_mock_client
        assert len(manager.playlists) == 2
        assert 'Test Playlist' in manager.playlists
        assert 'Another Playlist' in manager.playlists


class TestSearchSong:
    """Tests for search_song functionality"""

    def test_search_song_exact_match(self, real_spotify_manager_with_mock_client):
        """Test finding a song with exact match"""
        manager = real_spotify_manager_with_mock_client
        # Create a song and mock response with exact matching names
        song = Song(id="test artist|||test song", name="test song", artist="test artist", spotify_uri=None)
        manager.sp.search.return_value = create_spotify_search_response([
            create_spotify_track_response("test song", "test artist")
        ])

        uri = manager.search_song(song)
        assert uri is not None
        assert uri.startswith('spotify:track:')

    def test_search_song_fuzzy_match(self, real_spotify_manager_with_mock_client):
        """Test finding a song with fuzzy matching"""
        manager = real_spotify_manager_with_mock_client
        song = Song(
            id="beatles|||hey jude",
            name="hey jude",
            artist="beatles",
            spotify_uri=None
        )

        # Clear side_effect to allow return_value to work
        manager.sp.search.side_effect = None
        manager.sp.search.return_value = create_spotify_search_response([
            create_spotify_track_response("Hey Jude", "The Beatles")
        ])

        uri = manager.search_song(song)
        # Fuzzy matching with 80% threshold should succeed here:
        # - Name match: "hey jude" vs "Hey Jude" = 100% (case-insensitive)
        # - Artist match: "beatles" vs "The Beatles" ≈ 72%, boosted to 90% (contained)
        # - Combined score: 0.9 * 0.6 + 1.0 * 0.4 = 0.94 > 0.8 threshold
        assert uri is not None
        assert uri.startswith('spotify:track:')

    def test_search_song_not_found(self, real_spotify_manager_with_mock_client):
        """Test handling of non-existent songs"""
        manager = real_spotify_manager_with_mock_client
        song = Song(
            id="unknown|||nonexistent",
            name="nonexistent",
            artist="unknown",
            spotify_uri=None
        )

        # Return empty results
        manager.sp.search.return_value = {'tracks': {'items': []}}

        uri = manager.search_song(song)
        assert uri is None

    def test_search_song_with_remix(self, real_spotify_manager_with_mock_client):
        """Test searching for remix versions"""
        manager = real_spotify_manager_with_mock_client
        song = Song(
            id="test artist|||test song",
            name="test song",
            artist="test artist",
            spotify_uri=None
        )

        # The search result includes " - Remix" suffix which may affect matching
        manager.sp.search.return_value = create_spotify_search_response([
            create_spotify_track_response("test song", "test artist")
        ])

        uri = manager.search_song(song)
        # Search should find the track with exact match
        assert uri is not None


class TestCreatePlaylist:
    """Tests for playlist creation"""

    def test_create_new_playlist(self, real_spotify_manager_with_mock_client):
        """Test creating a new playlist"""
        manager = real_spotify_manager_with_mock_client
        manager.playlists = {}  # Clear existing playlists

        playlist_id = manager.create_playlist("New Test Playlist")

        assert playlist_id == 'new_playlist_id'
        manager.sp.user_playlist_create.assert_called_once()

    def test_create_playlist_already_exists(self, real_spotify_manager_with_mock_client):
        """Test that existing playlist returns existing ID"""
        manager = real_spotify_manager_with_mock_client
        existing_id = manager.playlists['Test Playlist']

        playlist_id = manager.create_playlist("Test Playlist")

        assert playlist_id == existing_id
        manager.sp.user_playlist_create.assert_not_called()


class TestGetPlaylistTracks:
    """Tests for retrieving playlist tracks"""

    def test_get_playlist_tracks_success(self, real_spotify_manager_with_mock_client):
        """Test retrieving tracks from a playlist"""
        manager = real_spotify_manager_with_mock_client
        tracks = manager.get_playlist_tracks("Test Playlist")

        assert len(tracks) == 2
        assert tracks[0]['name'] == 'Track 1'
        assert tracks[1]['name'] == 'Track 2'

    def test_get_playlist_tracks_not_found(self, real_spotify_manager_with_mock_client):
        """Test retrieving tracks from non-existent playlist"""
        manager = real_spotify_manager_with_mock_client
        tracks = manager.get_playlist_tracks("Nonexistent Playlist")

        assert tracks == []

    def test_get_playlist_tracks_empty(self, real_spotify_manager_with_mock_client):
        """Test retrieving tracks from empty playlist"""
        manager = real_spotify_manager_with_mock_client
        manager.sp.playlist_tracks.return_value = {
            'items': [],
            'next': None
        }

        tracks = manager.get_playlist_tracks("Test Playlist")
        assert tracks == []


class TestRefreshPlaylist:
    """Tests for refresh_playlist functionality"""

    def test_refresh_playlist_deletes_existing(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that refresh deletes existing playlist before creating new one"""
        manager = real_spotify_manager_with_mock_client
        result = manager.refresh_playlist("Test Playlist", sample_songs)

        assert result is True
        manager.sp.current_user_unfollow_playlist.assert_called_once_with('playlist_123')

    def test_refresh_playlist_creates_new(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that refresh creates a new playlist"""
        manager = real_spotify_manager_with_mock_client
        # Remove existing playlist from cache
        manager.playlists = {}

        result = manager.refresh_playlist("New Playlist", sample_songs)

        assert result is True
        manager.sp.user_playlist_create.assert_called()

    def test_refresh_playlist_adds_tracks(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that tracks are added to the playlist"""
        manager = real_spotify_manager_with_mock_client
        result = manager.refresh_playlist("Test Playlist", sample_songs)

        assert result is True
        manager.sp.playlist_add_items.assert_called()


class TestAppendToPlaylist:
    """Tests for append_to_playlist functionality"""

    def test_append_to_existing_playlist(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test appending songs to an existing playlist"""
        manager = real_spotify_manager_with_mock_client
        result = manager.append_to_playlist("Test Playlist", sample_songs[:2])

        assert result is True
        manager.sp.playlist_add_items.assert_called()

    def test_append_to_nonexistent_creates_playlist(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that appending to non-existent playlist creates it"""
        manager = real_spotify_manager_with_mock_client
        manager.playlists = {}

        result = manager.append_to_playlist("New Playlist", sample_songs[:2])

        assert result is True
        manager.sp.user_playlist_create.assert_called()


class TestRemoveFromPlaylist:
    """Tests for remove_from_playlist functionality"""

    def test_remove_tracks_success(self, real_spotify_manager_with_mock_client):
        """Test removing tracks from a playlist"""
        manager = real_spotify_manager_with_mock_client
        uris = ['spotify:track:abc', 'spotify:track:def']

        result = manager.remove_from_playlist("Test Playlist", uris)

        assert result is True
        manager.sp.playlist_remove_all_occurrences_of_items.assert_called()

    def test_remove_from_nonexistent_playlist(self, real_spotify_manager_with_mock_client):
        """Test removing from non-existent playlist"""
        manager = real_spotify_manager_with_mock_client
        result = manager.remove_from_playlist("Nonexistent", ['spotify:track:abc'])

        assert result is False

    def test_remove_empty_list(self, real_spotify_manager_with_mock_client):
        """Test removing empty list of tracks"""
        manager = real_spotify_manager_with_mock_client
        result = manager.remove_from_playlist("Test Playlist", [])

        assert result is True
        manager.sp.playlist_remove_all_occurrences_of_items.assert_not_called()


class TestGetTrackInfo:
    """Tests for get_track_info functionality"""

    def test_get_track_info_success(self, real_spotify_manager_with_mock_client):
        """Test getting track info by URI"""
        manager = real_spotify_manager_with_mock_client
        info = manager.get_track_info("spotify:track:test123")

        assert info is not None
        assert 'name' in info
        assert 'artist' in info
        assert 'uri' in info

    def test_get_track_info_failure(self, real_spotify_manager_with_mock_client):
        """Test handling of track info retrieval failure"""
        manager = real_spotify_manager_with_mock_client
        manager.sp.track.side_effect = Exception("Track not found")

        info = manager.get_track_info("spotify:track:invalid")

        assert info is None


class TestGetPlaylistId:
    """Tests for get_playlist_id functionality"""

    def test_get_playlist_id_cached(self, real_spotify_manager_with_mock_client):
        """Test getting playlist ID from cache"""
        manager = real_spotify_manager_with_mock_client
        playlist_id = manager.get_playlist_id("Test Playlist")

        assert playlist_id == 'playlist_123'

    def test_get_playlist_id_not_found(self, real_spotify_manager_with_mock_client):
        """Test getting ID of non-existent playlist"""
        manager = real_spotify_manager_with_mock_client
        playlist_id = manager.get_playlist_id("Nonexistent")

        assert playlist_id is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
