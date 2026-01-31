"""
Unit tests for plan and diff command workflows.
"""
from unittest.mock import MagicMock, patch, ANY


def test_plan_playlist_calls_simulate(mock_cli, sample_songs):
    mock_rm = MagicMock()
    mock_rm.simulate_generations.return_value = [sample_songs[:2]]

    with patch.object(mock_cli, "_get_rotation_manager", return_value=mock_rm):
        mock_cli.plan_playlist(
            "Test Playlist",
            song_count=2,
            fresh_days=30,
            generations=1,
            score_strategy="local",
            query="chill",
        )

    mock_rm.simulate_generations.assert_called_once_with(
        count=2,
        fresh_days=30,
        generations=1,
        score_config=ANY,
    )


def test_diff_playlist_uses_current_tracks(mock_cli, sample_songs):
    mock_rm = MagicMock()
    mock_rm.select_songs_for_today.return_value = sample_songs[:2]
    mock_cli._spotify.get_playlist_tracks.return_value = [
        {"uri": sample_songs[0].spotify_uri},
        {"uri": sample_songs[1].spotify_uri},
    ]

    with patch.object(mock_cli, "_get_rotation_manager", return_value=mock_rm):
        mock_cli.diff_playlist(
            "Test Playlist",
            song_count=2,
            fresh_days=30,
            score_strategy="local",
            query=None,
        )

    mock_rm.select_songs_for_today.assert_called_once_with(
        count=2,
        fresh_days=30,
        score_config=ANY,
    )
    mock_cli._spotify.get_playlist_tracks.assert_called_once_with("Test Playlist")
