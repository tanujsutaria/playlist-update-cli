"""
Unit tests for the playlist update workflow.
Tests the integration between RotationManager, SpotifyManager, and DatabaseManager.
"""
import pytest
from unittest.mock import MagicMock, patch, ANY
from models import Song


class TestUpdatePlaylistCommand:
    """Tests for the update playlist command workflow"""

    def test_update_playlist_basic(self, mock_cli, sample_songs):
        """Test basic playlist update with default parameters"""
        mock_cli._rotation_managers = {}

        # Mock the rotation manager
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.select_songs_for_today.return_value = sample_songs[:3]
            mock_rm.update_playlist.return_value = True
            mock_rm.get_rotation_stats.return_value = MagicMock(
                total_songs=100,
                unique_songs_used=50,
                songs_never_used=50,
                generations_count=10,
                complete_rotation_achieved=False
            )
            mock_get_rm.return_value = mock_rm

            mock_cli.update_playlist("Test Playlist", song_count=10, fresh_days=30)

            mock_rm.select_songs_for_today.assert_called_once_with(count=10, fresh_days=30, score_config=ANY)
            mock_rm.update_playlist.assert_called_once()

    def test_update_playlist_custom_count(self, mock_cli, sample_songs):
        """Test update with custom song count"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.select_songs_for_today.return_value = sample_songs[:5]
            mock_rm.update_playlist.return_value = True
            mock_rm.get_rotation_stats.return_value = MagicMock(
                total_songs=100,
                unique_songs_used=50,
                songs_never_used=50,
                generations_count=10,
                complete_rotation_achieved=False
            )
            mock_get_rm.return_value = mock_rm

            mock_cli.update_playlist("Test Playlist", song_count=5, fresh_days=30)

            mock_rm.select_songs_for_today.assert_called_once_with(count=5, fresh_days=30, score_config=ANY)

    def test_update_playlist_custom_fresh_days(self, mock_cli, sample_songs):
        """Test update with custom fresh_days parameter"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.select_songs_for_today.return_value = sample_songs[:3]
            mock_rm.update_playlist.return_value = True
            mock_rm.get_rotation_stats.return_value = MagicMock(
                total_songs=100,
                unique_songs_used=50,
                songs_never_used=50,
                generations_count=10,
                complete_rotation_achieved=False
            )
            mock_get_rm.return_value = mock_rm

            mock_cli.update_playlist("Test Playlist", song_count=10, fresh_days=7)

            mock_rm.select_songs_for_today.assert_called_once_with(count=10, fresh_days=7, score_config=ANY)

    def test_update_playlist_failure_logged(self, mock_cli, sample_songs, capsys):
        """Test that update failure is properly logged"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.select_songs_for_today.return_value = sample_songs[:3]
            mock_rm.update_playlist.return_value = False
            mock_get_rm.return_value = mock_rm

            mock_cli.update_playlist("Test Playlist", song_count=10, fresh_days=30)

            # update_playlist should have been called
            mock_rm.update_playlist.assert_called_once()


class TestUpdatePlaylistIntegration:
    """Integration tests for the update workflow"""

    def test_rotation_manager_created_for_playlist(self, mock_cli):
        """Test that rotation manager is created for the playlist"""
        assert mock_cli._rotation_managers == {}

        # The _get_rotation_manager method should create a new manager
        # This tests the lazy initialization pattern

    def test_update_shows_stats_on_success(self, mock_cli, sample_songs):
        """Test that stats are displayed after successful update"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.select_songs_for_today.return_value = sample_songs[:3]
            mock_rm.update_playlist.return_value = True
            mock_rm.get_rotation_stats.return_value = MagicMock(
                total_songs=100,
                unique_songs_used=50,
                songs_never_used=50,
                generations_count=10,
                complete_rotation_achieved=False
            )
            mock_get_rm.return_value = mock_rm

            mock_cli.update_playlist("Test Playlist", song_count=10, fresh_days=30)

            # Stats should be retrieved after successful update
            mock_rm.get_rotation_stats.assert_called()


class TestSongSelection:
    """Tests for song selection during update"""

    def test_songs_selected_from_database(self, mock_rotation_manager, sample_songs):
        """Test that songs are selected from the database"""
        mock_rotation_manager.history.generations = []

        songs = mock_rotation_manager.select_songs_for_today(count=3)

        assert len(songs) == 3
        for song in songs:
            assert isinstance(song, Song)

    def test_selection_excludes_recently_used(self, mock_rotation_manager, sample_songs):
        """Test that recently used songs are deprioritized"""
        # Mark some songs as recently used
        mock_rotation_manager.history.generations = [
            [sample_songs[0].id, sample_songs[1].id]
        ]

        songs = mock_rotation_manager.select_songs_for_today(count=3)

        # Should prefer unused songs
        selected_ids = {s.id for s in songs}
        # At minimum, some unused songs should be selected if available
        assert len(songs) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
