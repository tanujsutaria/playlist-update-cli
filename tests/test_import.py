"""
Unit tests for the import command.
Tests importing songs from CSV/TXT files with Spotify validation.
"""

from unittest.mock import MagicMock

import pytest

from test_mocks import create_spotify_artist_response, create_spotify_track_response


class TestImportValidFile:
    """Tests for importing valid files"""

    def test_import_valid_csv(self, mock_cli, tmp_path):
        """Test importing songs from a valid CSV file"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("song1,artist1\nsong2,artist2\n")

        # Mock successful Spotify search
        mock_cli._spotify.sp.search.return_value = {
            "tracks": {"items": [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        mock_cli.import_songs(str(csv_file))

        # Both valid rows pass validation and are added to the store.
        assert mock_cli._db.add_song.call_count == 2

    def test_import_valid_txt(self, mock_cli, tmp_path):
        """Test importing songs from a valid TXT file"""
        txt_file = tmp_path / "songs.txt"
        txt_file.write_text("song1,artist1\nsong2,artist2\n")

        mock_cli._spotify.sp.search.return_value = {
            "tracks": {"items": [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        mock_cli.import_songs(str(txt_file))

        # Both valid rows are added; the Song carries name/artist parsed from
        # the file (lowercased) and the URI from the Spotify search hit.
        assert mock_cli._db.add_song.call_count == 2
        added = [call.args[0] for call in mock_cli._db.add_song.call_args_list]
        assert {s.id for s in added} == {"artist1|||song1", "artist2|||song2"}
        assert all(s.spotify_uri for s in added)


class TestImportSkipsInvalidLines:
    """Tests for skipping invalid content"""

    def test_import_skips_comments(self, mock_cli, tmp_path):
        """Test that lines starting with # are skipped"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("# This is a comment\nsong1,artist1\n")

        mock_cli._spotify.sp.search.return_value = {
            "tracks": {"items": [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        mock_cli.import_songs(str(csv_file))

        # Only the real row is added; the comment line never reaches the store
        # or even a Spotify lookup.
        assert mock_cli._db.add_song.call_count == 1
        assert mock_cli._spotify.sp.search.call_count == 1

    def test_import_skips_empty_lines(self, mock_cli, tmp_path):
        """Test that empty lines are skipped"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("song1,artist1\n\n\nsong2,artist2\n")

        mock_cli._spotify.sp.search.return_value = {
            "tracks": {"items": [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        mock_cli.import_songs(str(csv_file))

        # The two blank lines are skipped; only the two real rows are added.
        assert mock_cli._db.add_song.call_count == 2

    def test_import_skips_malformed_lines(self, mock_cli, tmp_path):
        """Test that malformed lines (missing columns) are skipped"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("only_one_column\nsong1,artist1\n")

        mock_cli._spotify.sp.search.return_value = {
            "tracks": {"items": [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        mock_cli.import_songs(str(csv_file))

        # The single-column line is rejected before any add; only the valid row
        # is stored.
        assert mock_cli._db.add_song.call_count == 1
        added = mock_cli._db.add_song.call_args_list[0].args[0]
        assert added.id == "artist1|||song1"


class TestImportArtistValidation:
    """Tests for artist popularity validation"""

    def test_import_rejects_popular_artist(self, mock_cli, tmp_path):
        """Test that artists with >= 1M followers are rejected"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("hit song,popular artist\n")

        mock_cli._spotify.sp.search.return_value = {
            "tracks": {"items": [create_spotify_track_response("hit song", "popular artist")]}
        }
        # Artist has 2,000,000 followers - should be rejected
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response(
            "popular artist", 2000000
        )

        mock_cli.import_songs(str(csv_file))

        # Artist exceeds the 1M-follower cap, so the song is rejected: the
        # Spotify lookup happens but nothing is stored.
        assert mock_cli._spotify.sp.search.called
        assert mock_cli._db.add_song.call_count == 0

    def test_import_accepts_unpopular_artist(self, mock_cli, tmp_path):
        """Test that artists with < 1M followers are accepted"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("indie song,indie artist\n")

        mock_cli._spotify.sp.search.return_value = {
            "tracks": {"items": [create_spotify_track_response("indie song", "indie artist")]}
        }
        # Artist has 500k followers - should be accepted
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response(
            "indie artist", 500000
        )

        mock_cli.import_songs(str(csv_file))

        # Under the cap -> stored once.
        assert mock_cli._db.add_song.call_count == 1
        assert mock_cli._db.add_song.call_args_list[0].args[0].id == "indie artist|||indie song"


class TestImportSpotifyValidation:
    """Tests for Spotify song validation"""

    def test_import_skips_not_found_in_spotify(self, mock_cli, tmp_path):
        """Test that songs not found in Spotify are skipped"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("nonexistent song,unknown artist\n")

        # Spotify returns no results. The conftest mock installs a default
        # side_effect on search(); clear it so return_value applies.
        mock_cli._spotify.sp.search.side_effect = None
        mock_cli._spotify.sp.search.return_value = {"tracks": {"items": []}}

        mock_cli.import_songs(str(csv_file))

        # No Spotify match -> nothing added.
        assert mock_cli._db.add_song.call_count == 0

    def test_import_stores_spotify_uri(self, mock_cli, tmp_path):
        """Test that found Spotify URI is stored with the song"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("found song,known artist\n")

        track = create_spotify_track_response("found song", "known artist")
        # Clear the conftest default side_effect so our return_value applies.
        mock_cli._spotify.sp.search.side_effect = None
        mock_cli._spotify.sp.search.return_value = {"tracks": {"items": [track]}}
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response(
            "known artist", 500000
        )

        mock_cli.import_songs(str(csv_file))

        # The exact URI returned by the Spotify search hit is persisted on the
        # stored Song.
        assert mock_cli._db.add_song.call_count == 1
        stored = mock_cli._db.add_song.call_args_list[0].args[0]
        assert stored.spotify_uri == track["uri"]
        assert stored.id == "known artist|||found song"


class TestImportDuplicates:
    """Tests for duplicate handling"""

    def test_import_skips_existing_songs(self, mock_cli, tmp_path, sample_songs):
        """Test that songs already in database are skipped"""
        csv_file = tmp_path / "songs.csv"
        # Use an existing song ID
        csv_file.write_text("song1,artist1\n")

        mock_cli._spotify.sp.search.return_value = {
            "tracks": {"items": [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        # add_song returns False for duplicates
        mock_cli._db.add_song = MagicMock(return_value=False)

        mock_cli.import_songs(str(csv_file))

        # add_song is still invoked; the store reports the row as a duplicate
        # (returns False) rather than the importer pre-filtering it.
        assert mock_cli._db.add_song.call_count == 1


class TestImportErrorHandling:
    """Tests for error handling"""

    def test_import_file_not_found(self, mock_cli, capsys):
        """Test handling of missing file"""
        mock_cli.import_songs("/nonexistent/path/songs.csv")

        # Missing file returns early: no Spotify lookup, no store writes.
        assert mock_cli._spotify.sp.search.call_count == 0
        assert mock_cli._db.add_song.call_count == 0

    def test_import_handles_api_error(self, mock_cli, tmp_path):
        """Test handling of Spotify API errors"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("song1,artist1\n")

        mock_cli._spotify.sp.search.side_effect = Exception("API Error")

        # The per-line error is caught; the row is not stored and no exception
        # propagates out of import_songs.
        mock_cli.import_songs(str(csv_file))
        assert mock_cli._db.add_song.call_count == 0


class TestImportStatistics:
    """Tests for import statistics reporting"""

    def test_import_tracks_statistics(self, mock_cli, tmp_path, capsys):
        """Test that import tracks and reports statistics"""
        csv_file = tmp_path / "songs.csv"
        csv_file.write_text("song1,artist1\nsong2,artist2\n")

        mock_cli._spotify.sp.search.return_value = {
            "tracks": {"items": [create_spotify_track_response("song1", "artist1")]}
        }
        mock_cli._spotify.sp.artist.return_value = create_spotify_artist_response("artist1", 500000)

        mock_cli.import_songs(str(csv_file))

        # The summary table is rendered with the counts (2 entries, 2 added).
        out = capsys.readouterr().out
        assert "Import Summary" in out
        assert "Total entries processed" in out
        assert "Songs added" in out
        assert mock_cli._db.add_song.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
