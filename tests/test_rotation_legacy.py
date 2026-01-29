"""
Unit tests for RotationManager.
Tests the song rotation algorithm and history management with mocked dependencies.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from pathlib import Path

from models import Song, PlaylistHistory, RotationStats


class TestRotationStats:
    """Tests for get_rotation_stats functionality"""

    def test_stats_empty_history(self, mock_rotation_manager):
        """Test stats with no history"""
        mock_rotation_manager.history.generations = []

        stats = mock_rotation_manager.get_rotation_stats()

        assert stats.generations_count == 0
        assert stats.unique_songs_used == 0

    def test_stats_with_generations(self, mock_rotation_manager):
        """Test stats calculation with existing generations"""
        stats = mock_rotation_manager.get_rotation_stats()

        assert stats.generations_count == 3
        assert stats.unique_songs_used > 0
        assert stats.current_strategy == "similarity-based"

    def test_stats_complete_rotation(self, mock_rotation_manager, sample_songs):
        """Test that complete rotation is detected when all songs used"""
        # Use all songs in history
        all_song_ids = [s.id for s in sample_songs]
        mock_rotation_manager.history.generations = [all_song_ids]

        stats = mock_rotation_manager.get_rotation_stats()

        assert stats.complete_rotation_achieved is True
        assert stats.songs_never_used == 0

    def test_stats_songs_never_used(self, mock_rotation_manager, sample_songs):
        """Test calculation of songs never used"""
        # Only use some songs
        mock_rotation_manager.history.generations = [[sample_songs[0].id]]

        stats = mock_rotation_manager.get_rotation_stats()

        assert stats.songs_never_used == len(sample_songs) - 1


class TestSelectSongsForToday:
    """Tests for the song selection algorithm"""

    def test_select_prioritizes_never_used(self, mock_rotation_manager, sample_songs):
        """Test that never-used songs are prioritized (Tier 1)"""
        # Clear history so all songs are "never used"
        mock_rotation_manager.history.generations = []

        songs = mock_rotation_manager.select_songs_for_today(count=3)

        assert len(songs) == 3
        # All returned songs should be from the database
        song_ids = {s.id for s in songs}
        db_song_ids = {s.id for s in sample_songs}
        assert song_ids.issubset(db_song_ids)

    def test_select_respects_fresh_days(self, mock_rotation_manager, sample_songs):
        """Test that fresh_days parameter affects selection (Tier 2)"""
        # Add all songs to history (used recently)
        all_ids = [s.id for s in sample_songs]
        mock_rotation_manager.history.generations = [all_ids]

        # With fresh_days=0, all songs are "stale"
        songs = mock_rotation_manager.select_songs_for_today(count=3, fresh_days=0)

        assert len(songs) <= 3

    def test_select_returns_requested_count(self, mock_rotation_manager):
        """Test that selection returns the requested number of songs"""
        mock_rotation_manager.history.generations = []

        songs = mock_rotation_manager.select_songs_for_today(count=3)

        assert len(songs) == 3

    def test_select_with_partial_history(self, mock_rotation_manager, sample_songs):
        """Test selection when some songs are in history"""
        # Mark first two songs as used
        mock_rotation_manager.history.generations = [
            [sample_songs[0].id, sample_songs[1].id]
        ]

        songs = mock_rotation_manager.select_songs_for_today(count=3)

        # Should prioritize unused songs
        assert len(songs) == 3


class TestUpdatePlaylist:
    """Tests for update_playlist functionality"""

    def test_update_playlist_success(self, mock_rotation_manager, sample_songs):
        """Test successful playlist update"""
        result = mock_rotation_manager.update_playlist(sample_songs[:3])

        assert result is True
        mock_rotation_manager.spotify.refresh_playlist.assert_called_once()

    def test_update_playlist_records_generation(self, mock_rotation_manager, sample_songs):
        """Test that update records new generation in history"""
        initial_gen_count = len(mock_rotation_manager.history.generations)

        mock_rotation_manager.update_playlist(sample_songs[:3], record_generation=True)

        assert len(mock_rotation_manager.history.generations) == initial_gen_count + 1

    def test_update_playlist_skip_record(self, mock_rotation_manager, sample_songs):
        """Test that record_generation=False skips history update"""
        initial_gen_count = len(mock_rotation_manager.history.generations)

        mock_rotation_manager.update_playlist(sample_songs[:3], record_generation=False)

        # Generation count should not increase
        assert len(mock_rotation_manager.history.generations) == initial_gen_count

    def test_update_playlist_saves_history(self, mock_rotation_manager, sample_songs):
        """Test that history is saved after update"""
        mock_rotation_manager.update_playlist(sample_songs[:3])

        mock_rotation_manager._save_history.assert_called()

    def test_update_playlist_failure(self, mock_rotation_manager, sample_songs):
        """Test handling of playlist update failure"""
        mock_rotation_manager.spotify.refresh_playlist.return_value = False

        result = mock_rotation_manager.update_playlist(sample_songs[:3])

        assert result is False


class TestGetRecentGenerations:
    """Tests for get_recent_generations functionality"""

    def test_get_recent_generations_default(self, mock_rotation_manager):
        """Test getting recent generations with default count"""
        generations = mock_rotation_manager.get_recent_generations()

        assert len(generations) <= 5

    def test_get_recent_generations_custom_count(self, mock_rotation_manager):
        """Test getting specific number of generations"""
        generations = mock_rotation_manager.get_recent_generations(count=2)

        assert len(generations) <= 2

    def test_get_recent_generations_empty_history(self, mock_rotation_manager):
        """Test with empty history"""
        mock_rotation_manager.history.generations = []

        generations = mock_rotation_manager.get_recent_generations()

        assert generations == []


class TestGetRecentSongs:
    """Tests for get_recent_songs functionality"""

    def test_get_recent_songs_returns_dict(self, mock_rotation_manager):
        """Test that recent songs are returned as dict grouped by date"""
        songs_by_date = mock_rotation_manager.get_recent_songs(days=7)

        assert isinstance(songs_by_date, dict)

    def test_get_recent_songs_empty_history(self, mock_rotation_manager):
        """Test with empty history"""
        mock_rotation_manager.history.generations = []

        songs_by_date = mock_rotation_manager.get_recent_songs(days=7)

        assert songs_by_date == {}

    def test_get_recent_songs_date_format(self, mock_rotation_manager):
        """Test that dates are in correct format"""
        songs_by_date = mock_rotation_manager.get_recent_songs(days=3)

        for date_str in songs_by_date.keys():
            # Should be YYYY-MM-DD format
            datetime.strptime(date_str, "%Y-%m-%d")


class TestHistoryPersistence:
    """Tests for history save/load functionality"""

    def test_history_initialization(self, mock_rotation_manager):
        """Test that history is properly initialized"""
        assert mock_rotation_manager.history is not None
        assert isinstance(mock_rotation_manager.history, PlaylistHistory)

    def test_history_has_required_fields(self, mock_rotation_manager):
        """Test that history has all required fields"""
        history = mock_rotation_manager.history

        assert hasattr(history, 'playlist_id')
        assert hasattr(history, 'name')
        assert hasattr(history, 'generations')
        assert hasattr(history, 'current_generation')


class TestPlaylistHistoryModel:
    """Tests for PlaylistHistory model"""

    def test_all_used_songs_property(self, sample_playlist_history):
        """Test the all_used_songs property"""
        used = sample_playlist_history.all_used_songs

        assert isinstance(used, set)
        # Should contain all unique song IDs from all generations
        expected_count = len(set(
            sid for gen in sample_playlist_history.generations for sid in gen
        ))
        assert len(used) == expected_count

    def test_all_used_songs_empty(self):
        """Test all_used_songs with empty generations"""
        history = PlaylistHistory(
            playlist_id="test",
            name="Test",
            generations=[],
            current_generation=0
        )

        assert history.all_used_songs == set()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
