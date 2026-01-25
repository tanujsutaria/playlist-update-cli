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

    def test_initialization_with_mocked_client(self, mock_spotify_manager):
        """Verify SpotifyManager initializes correctly with mocked client"""
        assert mock_spotify_manager.user_id == 'test_user_id'
        assert 'Test Playlist' in mock_spotify_manager.playlists
        assert mock_spotify_manager.playlists['Test Playlist'] == 'playlist_123'

    def test_playlists_loaded_on_init(self, mock_spotify_manager):
        """Verify playlists are loaded during initialization"""
        assert len(mock_spotify_manager.playlists) == 2
        assert 'Test Playlist' in mock_spotify_manager.playlists
        assert 'Another Playlist' in mock_spotify_manager.playlists


class TestSearchSong:
    """Tests for search_song functionality"""

    def test_search_song_exact_match(self, mock_spotify_manager, sample_song):
        """Test finding a song with exact match"""
        # Configure mock to return matching result
        mock_spotify_manager.sp.search.return_value = create_spotify_search_response([
            create_spotify_track_response(sample_song.name, sample_song.artist)
        ])

        uri = mock_spotify_manager.search_song(sample_song)
        assert uri is not None
        assert uri.startswith('spotify:track:')

    def test_search_song_fuzzy_match(self, mock_spotify_manager):
        """Test finding a song with fuzzy matching"""
        song = Song(
            id="beatles|||hey jude",
            name="hey jude",
            artist="beatles",
            spotify_uri=None
        )

        # Return slightly different name but same artist
        mock_spotify_manager.sp.search.return_value = create_spotify_search_response([
            create_spotify_track_response("Hey Jude - Remastered", "beatles")
        ])

        uri = mock_spotify_manager.search_song(song)
        # Should find match due to artist matching
        assert uri is not None

    def test_search_song_not_found(self, mock_spotify_manager):
        """Test handling of non-existent songs"""
        song = Song(
            id="unknown|||nonexistent",
            name="nonexistent",
            artist="unknown",
            spotify_uri=None
        )

        # Return empty results
        mock_spotify_manager.sp.search.return_value = {'tracks': {'items': []}}

        uri = mock_spotify_manager.search_song(song)
        assert uri is None

    def test_search_song_with_remix(self, mock_spotify_manager):
        """Test searching for remix versions"""
        song = Song(
            id="artist|||song - remix",
            name="song - remix",
            artist="artist",
            spotify_uri=None
        )

        mock_spotify_manager.sp.search.return_value = create_spotify_search_response([
            create_spotify_track_response("song - remix", "artist")
        ])

        uri = mock_spotify_manager.search_song(song)
        assert uri is not None


class TestCreatePlaylist:
    """Tests for playlist creation"""

    def test_create_new_playlist(self, mock_spotify_manager):
        """Test creating a new playlist"""
        mock_spotify_manager.playlists = {}  # Clear existing playlists

        playlist_id = mock_spotify_manager.create_playlist("New Test Playlist")

        assert playlist_id == 'new_playlist_id'
        mock_spotify_manager.sp.user_playlist_create.assert_called_once()

    def test_create_playlist_already_exists(self, mock_spotify_manager):
        """Test that existing playlist returns existing ID"""
        existing_id = mock_spotify_manager.playlists['Test Playlist']

        playlist_id = mock_spotify_manager.create_playlist("Test Playlist")

        assert playlist_id == existing_id
        mock_spotify_manager.sp.user_playlist_create.assert_not_called()


class TestGetPlaylistTracks:
    """Tests for retrieving playlist tracks"""

    def test_get_playlist_tracks_success(self, mock_spotify_manager):
        """Test retrieving tracks from a playlist"""
        tracks = mock_spotify_manager.get_playlist_tracks("Test Playlist")

        assert len(tracks) == 2
        assert tracks[0]['name'] == 'Track 1'
        assert tracks[1]['name'] == 'Track 2'

    def test_get_playlist_tracks_not_found(self, mock_spotify_manager):
        """Test retrieving tracks from non-existent playlist"""
        tracks = mock_spotify_manager.get_playlist_tracks("Nonexistent Playlist")

        assert tracks == []

    def test_get_playlist_tracks_empty(self, mock_spotify_manager):
        """Test retrieving tracks from empty playlist"""
        mock_spotify_manager.sp.playlist_tracks.return_value = {
            'items': [],
            'next': None
        }

        tracks = mock_spotify_manager.get_playlist_tracks("Test Playlist")
        assert tracks == []


class TestRefreshPlaylist:
    """Tests for refresh_playlist functionality"""

    def test_refresh_playlist_deletes_existing(self, mock_spotify_manager, sample_songs):
        """Test that refresh deletes existing playlist before creating new one"""
        result = mock_spotify_manager.refresh_playlist("Test Playlist", sample_songs)

        assert result is True
        mock_spotify_manager.sp.current_user_unfollow_playlist.assert_called_once_with('playlist_123')

    def test_refresh_playlist_creates_new(self, mock_spotify_manager, sample_songs):
        """Test that refresh creates a new playlist"""
        # Remove existing playlist from cache
        mock_spotify_manager.playlists = {}

        result = mock_spotify_manager.refresh_playlist("New Playlist", sample_songs)

        assert result is True
        mock_spotify_manager.sp.user_playlist_create.assert_called()

    def test_refresh_playlist_adds_tracks(self, mock_spotify_manager, sample_songs):
        """Test that tracks are added to the playlist"""
        result = mock_spotify_manager.refresh_playlist("Test Playlist", sample_songs)

        assert result is True
        mock_spotify_manager.sp.playlist_add_items.assert_called()


class TestAppendToPlaylist:
    """Tests for append_to_playlist functionality"""

    def test_append_to_existing_playlist(self, mock_spotify_manager, sample_songs):
        """Test appending songs to an existing playlist"""
        result = mock_spotify_manager.append_to_playlist("Test Playlist", sample_songs[:2])

        assert result is True
        mock_spotify_manager.sp.playlist_add_items.assert_called()

    def test_append_to_nonexistent_creates_playlist(self, mock_spotify_manager, sample_songs):
        """Test that appending to non-existent playlist creates it"""
        mock_spotify_manager.playlists = {}

        result = mock_spotify_manager.append_to_playlist("New Playlist", sample_songs[:2])

        assert result is True
        mock_spotify_manager.sp.user_playlist_create.assert_called()


class TestRemoveFromPlaylist:
    """Tests for remove_from_playlist functionality"""

    def test_remove_tracks_success(self, mock_spotify_manager):
        """Test removing tracks from a playlist"""
        uris = ['spotify:track:abc', 'spotify:track:def']

        result = mock_spotify_manager.remove_from_playlist("Test Playlist", uris)

        assert result is True
        mock_spotify_manager.sp.playlist_remove_all_occurrences_of_items.assert_called()

    def test_remove_from_nonexistent_playlist(self, mock_spotify_manager):
        """Test removing from non-existent playlist"""
        result = mock_spotify_manager.remove_from_playlist("Nonexistent", ['spotify:track:abc'])

        assert result is False

    def test_remove_empty_list(self, mock_spotify_manager):
        """Test removing empty list of tracks"""
        result = mock_spotify_manager.remove_from_playlist("Test Playlist", [])

        assert result is True
        mock_spotify_manager.sp.playlist_remove_all_occurrences_of_items.assert_not_called()


class TestGetTrackInfo:
    """Tests for get_track_info functionality"""

    def test_get_track_info_success(self, mock_spotify_manager):
        """Test getting track info by URI"""
        info = mock_spotify_manager.get_track_info("spotify:track:test123")

        assert info is not None
        assert 'name' in info
        assert 'artist' in info
        assert 'uri' in info

    def test_get_track_info_failure(self, mock_spotify_manager):
        """Test handling of track info retrieval failure"""
        mock_spotify_manager.sp.track.side_effect = Exception("Track not found")

        info = mock_spotify_manager.get_track_info("spotify:track:invalid")

        assert info is None


class TestGetPlaylistId:
    """Tests for get_playlist_id functionality"""

    def test_get_playlist_id_cached(self, mock_spotify_manager):
        """Test getting playlist ID from cache"""
        playlist_id = mock_spotify_manager.get_playlist_id("Test Playlist")

        assert playlist_id == 'playlist_123'

    def test_get_playlist_id_not_found(self, mock_spotify_manager):
        """Test getting ID of non-existent playlist"""
        playlist_id = mock_spotify_manager.get_playlist_id("Nonexistent")

        assert playlist_id is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
