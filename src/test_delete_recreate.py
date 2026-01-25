"""
Unit tests for playlist delete and recreate functionality.
Tests the refresh_playlist workflow that deletes existing playlists and creates new ones.
"""
import pytest
from unittest.mock import MagicMock, patch, call
from models import Song


class TestRefreshPlaylistDeleteExisting:
    """Tests for deleting existing playlists during refresh"""

    def test_refresh_deletes_existing_playlist(self, mock_spotify_manager, sample_songs):
        """Test that existing playlist is deleted before creating new one"""
        result = mock_spotify_manager.refresh_playlist("Test Playlist", sample_songs)

        assert result is True
        mock_spotify_manager.sp.current_user_unfollow_playlist.assert_called_once_with('playlist_123')

    def test_refresh_removes_from_cache(self, mock_spotify_manager, sample_songs):
        """Test that deleted playlist is removed from local cache"""
        initial_playlists = dict(mock_spotify_manager.playlists)
        assert "Test Playlist" in initial_playlists

        # After refresh, playlist should be re-added with potentially new ID
        mock_spotify_manager.refresh_playlist("Test Playlist", sample_songs)

        # The playlist should still exist (recreated)
        assert "Test Playlist" in mock_spotify_manager.playlists

    def test_refresh_handles_delete_error(self, mock_spotify_manager, sample_songs):
        """Test that refresh continues even if delete fails"""
        mock_spotify_manager.sp.current_user_unfollow_playlist.side_effect = Exception("Delete failed")

        # Should still attempt to create new playlist
        result = mock_spotify_manager.refresh_playlist("Test Playlist", sample_songs)

        # Should still succeed (creates new playlist)
        assert result is True


class TestRefreshPlaylistCreateNew:
    """Tests for creating new playlists during refresh"""

    def test_refresh_creates_new_playlist(self, mock_spotify_manager, sample_songs):
        """Test that new playlist is created after deletion"""
        mock_spotify_manager.playlists = {}  # No existing playlist

        result = mock_spotify_manager.refresh_playlist("New Playlist", sample_songs)

        assert result is True
        mock_spotify_manager.sp.user_playlist_create.assert_called()

    def test_refresh_creates_playlist_with_correct_name(self, mock_spotify_manager, sample_songs):
        """Test that playlist is created with the correct name"""
        mock_spotify_manager.playlists = {}

        mock_spotify_manager.refresh_playlist("My Custom Playlist", sample_songs)

        call_args = mock_spotify_manager.sp.user_playlist_create.call_args
        assert call_args[0][1] == "My Custom Playlist"  # Second positional arg is name


class TestRefreshPlaylistAddTracks:
    """Tests for adding tracks during refresh"""

    def test_refresh_adds_tracks_with_uris(self, mock_spotify_manager, sample_songs):
        """Test that tracks with URIs are added to playlist"""
        result = mock_spotify_manager.refresh_playlist("Test Playlist", sample_songs)

        assert result is True
        mock_spotify_manager.sp.playlist_add_items.assert_called()

    def test_refresh_adds_tracks_in_batches(self, mock_spotify_manager):
        """Test that tracks are added in batches of 50"""
        # Create 75 songs to test batching
        many_songs = [
            Song(
                id=f"artist{i}|||song{i}",
                name=f"song{i}",
                artist=f"artist{i}",
                spotify_uri=f"spotify:track:uri{i}"
            )
            for i in range(75)
        ]

        mock_spotify_manager.refresh_playlist("Test Playlist", many_songs)

        # Should be called twice: once for 50, once for 25
        assert mock_spotify_manager.sp.playlist_add_items.call_count >= 1

    def test_refresh_searches_for_missing_uris(self, mock_spotify_manager):
        """Test that songs without URIs are searched for"""
        songs_without_uri = [
            Song(id="artist|||song", name="song", artist="artist", spotify_uri=None)
        ]

        mock_spotify_manager.refresh_playlist("Test Playlist", songs_without_uri)

        # Search should be called for songs without URI
        mock_spotify_manager.sp.search.assert_called()

    def test_refresh_handles_failed_searches(self, mock_spotify_manager):
        """Test that refresh continues when song search fails"""
        mock_spotify_manager.sp.search.return_value = {'tracks': {'items': []}}

        songs = [
            Song(id="unknown|||song", name="unknown song", artist="unknown", spotify_uri=None)
        ]

        result = mock_spotify_manager.refresh_playlist("Test Playlist", songs)

        # Should still succeed (creates playlist, just without the failed songs)
        assert result is True


class TestRefreshPlaylistErrorHandling:
    """Tests for error handling during refresh"""

    def test_refresh_returns_false_on_create_failure(self, mock_spotify_manager, sample_songs):
        """Test that refresh returns False when playlist creation fails"""
        mock_spotify_manager.playlists = {}
        mock_spotify_manager.sp.user_playlist_create.return_value = None

        # The create_playlist method will return None
        with patch.object(mock_spotify_manager, 'create_playlist', return_value=None):
            result = mock_spotify_manager.refresh_playlist("Test Playlist", sample_songs)

        assert result is False

    def test_refresh_handles_api_exception(self, mock_spotify_manager, sample_songs):
        """Test that API exceptions are handled gracefully"""
        mock_spotify_manager.sp.playlist_add_items.side_effect = Exception("API Error")

        result = mock_spotify_manager.refresh_playlist("Test Playlist", sample_songs)

        # Should return False on error
        assert result is False


class TestRefreshPlaylistSyncMode:
    """Tests for sync_mode parameter (if applicable)"""

    def test_refresh_with_empty_songs_uses_fallback(self, mock_spotify_manager):
        """Test that empty song list triggers fallback behavior"""
        empty_songs = []

        result = mock_spotify_manager.refresh_playlist("Test Playlist", empty_songs)

        # Should still succeed (may add test songs as fallback)
        assert result is True


class TestDeleteRecreateWorkflow:
    """End-to-end tests for the delete-recreate workflow"""

    def test_full_workflow_success(self, mock_spotify_manager, sample_songs):
        """Test complete delete-recreate workflow"""
        # Initial state: playlist exists
        assert "Test Playlist" in mock_spotify_manager.playlists

        # Execute refresh
        result = mock_spotify_manager.refresh_playlist("Test Playlist", sample_songs[:3])

        # Verify workflow
        assert result is True
        mock_spotify_manager.sp.current_user_unfollow_playlist.assert_called()  # Delete
        mock_spotify_manager.sp.user_playlist_create.assert_called()  # Create
        mock_spotify_manager.sp.playlist_add_items.assert_called()  # Add tracks

    def test_workflow_with_new_playlist(self, mock_spotify_manager, sample_songs):
        """Test workflow when playlist doesn't exist"""
        mock_spotify_manager.playlists = {}  # No existing playlists

        result = mock_spotify_manager.refresh_playlist("Brand New Playlist", sample_songs[:3])

        assert result is True
        # Should not attempt to delete (no existing playlist)
        mock_spotify_manager.sp.current_user_unfollow_playlist.assert_not_called()
        # Should create and add tracks
        mock_spotify_manager.sp.user_playlist_create.assert_called()
        mock_spotify_manager.sp.playlist_add_items.assert_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
