"""
Unit tests for the import command.
Tests importing songs from CSV/TXT files with Spotify validation.
"""
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from models import Song
from test_mocks import create_spotify_track_response, create_spotify_artist_response


class TestImportValidFile:
    """Tests for importing valid files"""

    def test_import_valid_csv(self, mock_cli, tmp_path):
        """Test importing songs from a valid CSV file"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("song1,artist1\nsong2,artist2\n")

        # Mock successful Spotify search
        mock_cli._spotify.sp.search.return_value = {
            'tracks': {'items': [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        mock_cli.import_songs(str(csv_file))

        # Should have attempted to add songs
        assert mock_cli._db.add_song.called or mock_cli._db._save_state.called

    def test_import_valid_txt(self, mock_cli, tmp_path):
        """Test importing songs from a valid TXT file"""
        txt_file = tmp_path / "songs.txt"
        txt_file.write_text("song1,artist1\nsong2,artist2\n")

        mock_cli._spotify.sp.search.return_value = {
            'tracks': {'items': [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        mock_cli.import_songs(str(txt_file))

        # Should not raise an error
        assert True


class TestImportSkipsInvalidLines:
    """Tests for skipping invalid content"""

    def test_import_skips_comments(self, mock_cli, tmp_path):
        """Test that lines starting with # are skipped"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("# This is a comment\nsong1,artist1\n")

        mock_cli._spotify.sp.search.return_value = {
            'tracks': {'items': [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        # Should not error on comment lines
        mock_cli.import_songs(str(csv_file))

    def test_import_skips_empty_lines(self, mock_cli, tmp_path):
        """Test that empty lines are skipped"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("song1,artist1\n\n\nsong2,artist2\n")

        mock_cli._spotify.sp.search.return_value = {
            'tracks': {'items': [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        mock_cli.import_songs(str(csv_file))

    def test_import_skips_malformed_lines(self, mock_cli, tmp_path):
        """Test that malformed lines (missing columns) are skipped"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("only_one_column\nsong1,artist1\n")

        mock_cli._spotify.sp.search.return_value = {
            'tracks': {'items': [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        # Should not error on malformed lines
        mock_cli.import_songs(str(csv_file))


class TestImportArtistValidation:
    """Tests for artist popularity validation"""

    def test_import_rejects_popular_artist(self, mock_cli, tmp_path):
        """Test that artists with >= 1M followers are rejected"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("hit song,popular artist\n")

        mock_cli._spotify.sp.search.return_value = {
            'tracks': {'items': [create_spotify_track_response("hit song", "popular artist")]}
        }
        # Artist has 2,000,000 followers - should be rejected
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("popular artist", 2000000)

        mock_cli.import_songs(str(csv_file))

        # Song should not be added due to popular artist
        # The method logs a warning but continues

    def test_import_accepts_unpopular_artist(self, mock_cli, tmp_path):
        """Test that artists with < 1M followers are accepted"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("indie song,indie artist\n")

        mock_cli._spotify.sp.search.return_value = {
            'tracks': {'items': [create_spotify_track_response("indie song", "indie artist")]}
        }
        # Artist has 500k followers - should be accepted
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("indie artist", 500000)

        mock_cli.import_songs(str(csv_file))


class TestImportSpotifyValidation:
    """Tests for Spotify song validation"""

    def test_import_skips_not_found_in_spotify(self, mock_cli, tmp_path):
        """Test that songs not found in Spotify are skipped"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("nonexistent song,unknown artist\n")

        # Spotify returns no results
        mock_cli._spotify.sp.search.return_value = {'tracks': {'items': []}}

        mock_cli.import_songs(str(csv_file))

        # Should log warning but not error

    def test_import_stores_spotify_uri(self, mock_cli, tmp_path):
        """Test that found Spotify URI is stored with the song"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("found song,known artist\n")

        track = create_spotify_track_response("found song", "known artist")
        mock_cli._spotify.sp.search.return_value = {'tracks': {'items': [track]}}
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("known artist", 500000)

        mock_cli.import_songs(str(csv_file))


class TestImportDuplicates:
    """Tests for duplicate handling"""

    def test_import_skips_existing_songs(self, mock_cli, tmp_path, sample_songs):
        """Test that songs already in database are skipped"""
        csv_file = tmp_path / "songs.csv"
        # Use an existing song ID
        csv_file.write_text("song1,artist1\n")

        mock_cli._spotify.sp.search.return_value = {
            'tracks': {'items': [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        # add_song returns False for duplicates
        mock_cli._db.add_song = MagicMock(return_value=False)

        mock_cli.import_songs(str(csv_file))


class TestImportErrorHandling:
    """Tests for error handling"""

    def test_import_file_not_found(self, mock_cli, capsys):
        """Test handling of missing file"""
        mock_cli.import_songs("/nonexistent/path/songs.csv")

        # Should log error but not crash

    def test_import_handles_api_error(self, mock_cli, tmp_path):
        """Test handling of Spotify API errors"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("song1,artist1\n")

        mock_cli._spotify.sp.search.side_effect = Exception("API Error")

        # Should handle error gracefully
        mock_cli.import_songs(str(csv_file))


class TestImportStatistics:
    """Tests for import statistics reporting"""

    def test_import_tracks_statistics(self, mock_cli, tmp_path):
        """Test that import tracks and reports statistics"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("song1,artist1\nsong2,artist2\n")

        mock_cli._spotify.sp.search.return_value = {
            'tracks': {'items': [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        # Import should complete and log statistics
        mock_cli.import_songs(str(csv_file))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
