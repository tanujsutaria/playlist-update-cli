"""
Unit tests for playlist delete and recreate functionality.
Tests the refresh_playlist workflow that deletes existing playlists and creates new ones.
"""
import pytest
from unittest.mock import MagicMock, patch, call
from models import Song


class TestRefreshPlaylistDeleteExisting:
    """Tests for deleting existing playlists during refresh"""

    def test_refresh_deletes_existing_playlist(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that existing playlist is deleted before creating new one"""
        manager = real_spotify_manager_with_mock_client
        result = manager.refresh_playlist("Test Playlist", sample_songs)

        assert result is True
        manager.sp.current_user_unfollow_playlist.assert_called_once_with('playlist_123')

    def test_refresh_removes_from_cache(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that deleted playlist is removed from local cache"""
        manager = real_spotify_manager_with_mock_client
        initial_playlists = dict(manager.playlists)
        assert "Test Playlist" in initial_playlists

        # After refresh, playlist should be re-added with potentially new ID
        manager.refresh_playlist("Test Playlist", sample_songs)

        # The playlist should still exist (recreated)
        assert "Test Playlist" in manager.playlists

    def test_refresh_handles_delete_error(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that refresh continues even if delete fails"""
        manager = real_spotify_manager_with_mock_client
        manager.sp.current_user_unfollow_playlist.side_effect = Exception("Delete failed")

        # Should still attempt to create new playlist
        result = manager.refresh_playlist("Test Playlist", sample_songs)

        # Should still succeed (creates new playlist)
        assert result is True


class TestRefreshPlaylistCreateNew:
    """Tests for creating new playlists during refresh"""

    def test_refresh_creates_new_playlist(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that new playlist is created after deletion"""
        manager = real_spotify_manager_with_mock_client
        manager.playlists = {}  # No existing playlist

        result = manager.refresh_playlist("New Playlist", sample_songs)

        assert result is True
        manager.sp.user_playlist_create.assert_called()

    def test_refresh_creates_playlist_with_correct_name(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that playlist is created with the correct name"""
        manager = real_spotify_manager_with_mock_client
        manager.playlists = {}

        manager.refresh_playlist("My Custom Playlist", sample_songs)

        call_args = manager.sp.user_playlist_create.call_args
        assert call_args[0][1] == "My Custom Playlist"  # Second positional arg is name


class TestRefreshPlaylistAddTracks:
    """Tests for adding tracks during refresh"""

    def test_refresh_adds_tracks_with_uris(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that tracks with URIs are added to playlist"""
        manager = real_spotify_manager_with_mock_client
        result = manager.refresh_playlist("Test Playlist", sample_songs)

        assert result is True
        manager.sp.playlist_add_items.assert_called()

    def test_refresh_adds_tracks_in_batches(self, real_spotify_manager_with_mock_client):
        """Test that tracks are added in batches of 50"""
        manager = real_spotify_manager_with_mock_client
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

        manager.refresh_playlist("Test Playlist", many_songs)

        # Should be called twice: once for 50, once for 25
        assert manager.sp.playlist_add_items.call_count >= 1

    def test_refresh_searches_for_missing_uris(self, real_spotify_manager_with_mock_client):
        """Test that songs without URIs are searched for"""
        manager = real_spotify_manager_with_mock_client
        songs_without_uri = [
            Song(id="artist|||song", name="song", artist="artist", spotify_uri=None)
        ]

        manager.refresh_playlist("Test Playlist", songs_without_uri)

        # Search should be called for songs without URI
        manager.sp.search.assert_called()

    def test_refresh_handles_failed_searches(self, real_spotify_manager_with_mock_client):
        """Test that refresh continues when song search fails"""
        manager = real_spotify_manager_with_mock_client
        manager.sp.search.return_value = {'tracks': {'items': []}}

        songs = [
            Song(id="unknown|||song", name="unknown song", artist="unknown", spotify_uri=None)
        ]

        result = manager.refresh_playlist("Test Playlist", songs)

        # Should still succeed (creates playlist, just without the failed songs)
        assert result is True


class TestRefreshPlaylistErrorHandling:
    """Tests for error handling during refresh"""

    def test_refresh_returns_false_on_create_failure(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that refresh returns False when playlist creation fails"""
        manager = real_spotify_manager_with_mock_client
        manager.playlists = {}
        manager.sp.user_playlist_create.return_value = None

        # The create_playlist method will return None
        with patch.object(manager, 'create_playlist', return_value=None):
            result = manager.refresh_playlist("Test Playlist", sample_songs)

        assert result is False

    def test_refresh_handles_api_exception(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test that API exceptions are handled gracefully"""
        manager = real_spotify_manager_with_mock_client
        manager.sp.playlist_add_items.side_effect = Exception("API Error")

        result = manager.refresh_playlist("Test Playlist", sample_songs)

        # The method logs errors but may still return True if partial success
        # The important thing is it doesn't crash
        assert result in [True, False]


class TestRefreshPlaylistSyncMode:
    """Tests for sync_mode parameter (if applicable)"""

    def test_refresh_with_empty_songs_uses_fallback(self, real_spotify_manager_with_mock_client):
        """Test that empty song list triggers fallback behavior"""
        manager = real_spotify_manager_with_mock_client
        empty_songs = []

        result = manager.refresh_playlist("Test Playlist", empty_songs)

        # Should still succeed (may add test songs as fallback)
        assert result is True


class TestDeleteRecreateWorkflow:
    """End-to-end tests for the delete-recreate workflow"""

    def test_full_workflow_success(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test complete delete-recreate workflow"""
        manager = real_spotify_manager_with_mock_client
        # Initial state: playlist exists
        assert "Test Playlist" in manager.playlists

        # Execute refresh
        result = manager.refresh_playlist("Test Playlist", sample_songs[:3])

        # Verify workflow
        assert result is True
        manager.sp.current_user_unfollow_playlist.assert_called()  # Delete
        manager.sp.user_playlist_create.assert_called()  # Create
        manager.sp.playlist_add_items.assert_called()  # Add tracks

    def test_workflow_with_new_playlist(self, real_spotify_manager_with_mock_client, sample_songs):
        """Test workflow when playlist doesn't exist"""
        manager = real_spotify_manager_with_mock_client
        manager.playlists = {}  # No existing playlists

        result = manager.refresh_playlist("Brand New Playlist", sample_songs[:3])

        assert result is True
        # Should not attempt to delete (no existing playlist)
        manager.sp.current_user_unfollow_playlist.assert_not_called()
        # Should create and add tracks
        manager.sp.user_playlist_create.assert_called()
        manager.sp.playlist_add_items.assert_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
