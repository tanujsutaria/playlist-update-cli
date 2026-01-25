"""
Unit tests for CLI commands: stats, view, sync, extract, clean, list-rotations, restore-previous-rotation.
"""
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from models import Song, RotationStats


class TestStatsCommand:
    """Tests for the stats command"""

    def test_stats_shows_database_stats(self, mock_cli, capsys):
        """Test that database stats are displayed"""
        mock_cli._db.get_stats.return_value = {
            'total_songs': 100,
            'embedding_dimensions': 384,
            'storage_size_mb': 2.5
        }

        mock_cli.show_stats()

        mock_cli._db.get_stats.assert_called_once()

    def test_stats_with_playlist(self, mock_cli):
        """Test stats with specific playlist"""
        mock_cli._db.get_stats.return_value = {
            'total_songs': 100,
            'embedding_dimensions': 384,
            'storage_size_mb': 2.5
        }

        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.get_rotation_stats.return_value = RotationStats(
                total_songs=100,
                unique_songs_used=50,
                generations_count=10,
                songs_never_used=50,
                complete_rotation_achieved=False,
                current_strategy="similarity-based"
            )
            mock_rm.get_recent_generations.return_value = []
            mock_get_rm.return_value = mock_rm

            mock_cli.show_stats("Test Playlist")

            mock_rm.get_rotation_stats.assert_called_once()

    def test_stats_empty_database(self, mock_cli, empty_database_manager):
        """Test stats with empty database"""
        mock_cli._db = empty_database_manager
        empty_database_manager.get_stats = MagicMock(return_value={
            'total_songs': 0,
            'embedding_dimensions': 0,
            'storage_size_mb': 0
        })

        mock_cli.show_stats()

        empty_database_manager.get_stats.assert_called_once()


class TestViewCommand:
    """Tests for the view command"""

    def test_view_playlist_with_tracks(self, mock_cli, capsys):
        """Test viewing a playlist with tracks"""
        mock_cli._spotify.get_playlist_tracks.return_value = [
            {'name': 'Song 1', 'artist': 'Artist 1', 'uri': 'uri1', 'added_at': '2024-01-01T00:00:00Z'},
            {'name': 'Song 2', 'artist': 'Artist 2', 'uri': 'uri2', 'added_at': '2024-01-02T00:00:00Z'},
        ]

        mock_cli.view_playlist("Test Playlist")

        mock_cli._spotify.get_playlist_tracks.assert_called_once_with("Test Playlist")

    def test_view_empty_playlist(self, mock_cli, capsys):
        """Test viewing an empty playlist"""
        mock_cli._spotify.get_playlist_tracks.return_value = []

        mock_cli.view_playlist("Empty Playlist")

        # Should show "empty" message

    def test_view_playlist_not_found(self, mock_cli):
        """Test viewing a non-existent playlist"""
        mock_cli._spotify.get_playlist_tracks.return_value = []

        mock_cli.view_playlist("Nonexistent")


class TestSyncCommand:
    """Tests for the sync command"""

    def test_sync_adds_new_songs(self, mock_cli, sample_songs):
        """Test that sync adds new songs from database to playlist"""
        mock_cli._db.get_all_songs.return_value = sample_songs
        mock_cli._spotify.get_playlist_tracks.return_value = []  # Empty playlist
        mock_cli._spotify.append_to_playlist.return_value = True

        mock_cli.sync_playlist("Test Playlist")

        mock_cli._spotify.append_to_playlist.assert_called()

    def test_sync_removes_deleted_songs(self, mock_cli, sample_songs):
        """Test that sync removes songs not in database"""
        mock_cli._db.get_all_songs.return_value = sample_songs[:2]  # Only 2 songs
        mock_cli._spotify.get_playlist_tracks.return_value = [
            {'name': 'Song 1', 'artist': 'Artist 1', 'uri': 'spotify:track:extra'},
        ]
        mock_cli._spotify.remove_from_playlist.return_value = True

        mock_cli.sync_playlist("Test Playlist")

    def test_sync_already_in_sync(self, mock_cli, sample_songs):
        """Test sync when playlist is already in sync"""
        mock_cli._db.get_all_songs.return_value = sample_songs
        # Return matching tracks
        mock_cli._spotify.get_playlist_tracks.return_value = [
            {'name': s.name, 'artist': s.artist, 'uri': s.spotify_uri}
            for s in sample_songs
        ]

        mock_cli.sync_playlist("Test Playlist")

    def test_sync_empty_database(self, mock_cli, empty_database_manager):
        """Test sync with empty database"""
        mock_cli._db = empty_database_manager
        empty_database_manager.get_all_songs = MagicMock(return_value=[])

        mock_cli.sync_playlist("Test Playlist")


class TestExtractCommand:
    """Tests for the extract command"""

    def test_extract_creates_csv(self, mock_cli, tmp_path):
        """Test that extract creates a CSV file"""
        mock_cli._spotify.get_playlist_tracks.return_value = [
            {'name': 'Song 1', 'artist': 'Artist 1', 'uri': 'uri1'},
            {'name': 'Song 2', 'artist': 'Artist 2', 'uri': 'uri2'},
        ]

        output_file = tmp_path / "output.csv"
        result = mock_cli.extract_playlist("Test Playlist", str(output_file))

        assert result is True
        assert output_file.exists()

    def test_extract_default_filename(self, mock_cli, tmp_path, monkeypatch):
        """Test that extract uses playlist name if no output specified"""
        mock_cli._spotify.get_playlist_tracks.return_value = [
            {'name': 'Song 1', 'artist': 'Artist 1', 'uri': 'uri1'},
        ]

        # Change to tmp directory
        monkeypatch.chdir(tmp_path)

        result = mock_cli.extract_playlist("Test Playlist", None)

        assert result is True

    def test_extract_adds_csv_extension(self, mock_cli, tmp_path):
        """Test that .csv extension is added if missing"""
        mock_cli._spotify.get_playlist_tracks.return_value = [
            {'name': 'Song 1', 'artist': 'Artist 1', 'uri': 'uri1'},
        ]

        output_file = tmp_path / "output"  # No extension
        result = mock_cli.extract_playlist("Test Playlist", str(output_file))

        assert result is True
        assert (tmp_path / "output.csv").exists()

    def test_extract_empty_playlist(self, mock_cli, tmp_path):
        """Test extract returns False for empty playlist"""
        mock_cli._spotify.get_playlist_tracks.return_value = []

        result = mock_cli.extract_playlist("Empty Playlist", str(tmp_path / "output.csv"))

        assert result is False


class TestCleanCommand:
    """Tests for the clean command"""

    def test_clean_dry_run(self, mock_cli, sample_songs):
        """Test clean with dry_run shows but doesn't remove"""
        mock_cli._db.get_all_songs.return_value = sample_songs
        mock_cli._spotify.sp.search.return_value = {'tracks': {'items': []}}  # Not found
        mock_cli._db.remove_song = MagicMock()

        mock_cli.clean_database(dry_run=True)

        # remove_song should NOT be called in dry run
        mock_cli._db.remove_song.assert_not_called()

    def test_clean_removes_not_found(self, mock_cli, sample_songs):
        """Test that songs not in Spotify are removed"""
        mock_cli._db.get_all_songs.return_value = sample_songs[:1]
        mock_cli._spotify.sp.search.return_value = {'tracks': {'items': []}}
        mock_cli._spotify.get_track_info = MagicMock(return_value=None)
        mock_cli._db.remove_song = MagicMock()

        mock_cli.clean_database(dry_run=False)

    def test_clean_removes_popular_artists(self, mock_cli, sample_songs):
        """Test that songs with popular artists are removed"""
        mock_cli._db.get_all_songs.return_value = sample_songs[:1]
        mock_cli._spotify.get_track_info = MagicMock(return_value={
            'name': 'test', 'artist': 'test', 'uri': 'uri'
        })
        # Popular artist
        mock_cli._spotify.sp.artist.return_value = {'followers': {'total': 2000000}}

    def test_clean_empty_database(self, mock_cli, empty_database_manager):
        """Test clean with empty database"""
        mock_cli._db = empty_database_manager
        empty_database_manager.get_all_songs = MagicMock(return_value=[])

        mock_cli.clean_database(dry_run=False)


class TestListRotationsCommand:
    """Tests for the list-rotations command"""

    def test_list_rotations_default(self, mock_cli, sample_songs):
        """Test listing rotations with default count"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.history.generations = [
                ["song1|||artist1", "song2|||artist2"],
                ["song3|||artist3", "song4|||artist4"],
            ]
            mock_rm.history.current_generation = 1
            mock_get_rm.return_value = mock_rm
            mock_cli._db.get_song_by_id = MagicMock(return_value=sample_songs[0])

            mock_cli.list_rotations("Test Playlist", "3")

    def test_list_rotations_all(self, mock_cli, sample_songs):
        """Test listing all rotations"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.history.generations = [["song1"], ["song2"], ["song3"]]
            mock_rm.history.current_generation = 2
            mock_get_rm.return_value = mock_rm
            mock_cli._db.get_song_by_id = MagicMock(return_value=sample_songs[0])

            mock_cli.list_rotations("Test Playlist", "all")

    def test_list_rotations_empty_history(self, mock_cli):
        """Test listing rotations with no history"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.history.generations = []
            mock_get_rm.return_value = mock_rm

            mock_cli.list_rotations("Test Playlist", "3")


class TestRestorePreviousRotationCommand:
    """Tests for the restore-previous-rotation command"""

    def test_restore_previous_default(self, mock_cli, sample_songs):
        """Test restoring previous generation"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.history.generations = [["song1"], ["song2"], ["song3"]]
            mock_rm.history.current_generation = 2
            mock_rm.update_playlist.return_value = True
            mock_get_rm.return_value = mock_rm
            mock_cli._db.get_song_by_id = MagicMock(return_value=sample_songs[0])

            mock_cli.restore_previous_rotation("Test Playlist", -1)

            mock_rm.update_playlist.assert_called()

    def test_restore_custom_offset(self, mock_cli, sample_songs):
        """Test restoring with custom offset"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.history.generations = [["song1"], ["song2"], ["song3"]]
            mock_rm.history.current_generation = 2
            mock_rm.update_playlist.return_value = True
            mock_get_rm.return_value = mock_rm
            mock_cli._db.get_song_by_id = MagicMock(return_value=sample_songs[0])

            mock_cli.restore_previous_rotation("Test Playlist", -2)

    def test_restore_out_of_bounds(self, mock_cli):
        """Test restore with invalid offset"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.history.generations = [["song1"]]
            mock_rm.history.current_generation = 0
            mock_get_rm.return_value = mock_rm

            # Offset -5 is out of bounds for 1 generation
            mock_cli.restore_previous_rotation("Test Playlist", -5)

            # update_playlist should NOT be called
            mock_rm.update_playlist.assert_not_called()

    def test_restore_updates_current_generation(self, mock_cli, sample_songs):
        """Test that restore updates current_generation"""
        with patch.object(mock_cli, '_get_rotation_manager') as mock_get_rm:
            mock_rm = MagicMock()
            mock_rm.history.generations = [["song1"], ["song2"], ["song3"]]
            mock_rm.history.current_generation = 2
            mock_rm.update_playlist.return_value = True
            mock_get_rm.return_value = mock_rm
            mock_cli._db.get_song_by_id = MagicMock(return_value=sample_songs[0])

            mock_cli.restore_previous_rotation("Test Playlist", -1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
