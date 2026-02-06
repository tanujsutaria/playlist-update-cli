"""
Unit tests for argument parsing.
Tests all CLI commands and their argument handling.
"""
import pytest
import sys
from unittest.mock import patch

from arg_parse import setup_parsers, parse_args, parse_tokens


class TestSetupParsers:
    """Tests for parser setup"""

    def test_parser_created(self):
        """Test that parser is created successfully"""
        parser = setup_parsers()
        assert parser is not None

    def test_all_commands_registered(self):
        """Test that all expected commands are registered"""
        parser = setup_parsers()

        expected_commands = [
            'import', 'update', 'stats', 'view', 'sync', 'extract',
            'clean', 'backup', 'restore', 'restore-previous-rotation',
            'list-rotations', 'list-backups', 'plan', 'diff',
            'auth-status', 'auth-refresh', 'search', 'debug', 'interactive',
            'ingest', 'listen-sync', 'rotate', 'rotate-played'
        ]

        # Get subparsers
        subparsers_actions = [
            action for action in parser._actions
            if isinstance(action, type(parser._subparsers))
        ]

        # The _subparsers action contains the choices
        for action in parser._actions:
            if hasattr(action, 'choices') and action.choices:
                for cmd in expected_commands:
                    assert cmd in action.choices, f"Command '{cmd}' not found in parser"


class TestImportCommand:
    """Tests for import command parsing"""

    def test_parse_import_with_file(self):
        """Test parsing import command with file argument"""
        parser = setup_parsers()
        args = parser.parse_args(['import', 'songs.csv'])

        assert args.command == 'import'
        assert args.file == 'songs.csv'

    def test_parse_import_requires_file(self):
        """Test that import requires file argument"""
        parser = setup_parsers()

        with pytest.raises(SystemExit):
            parser.parse_args(['import'])


class TestUpdateCommand:
    """Tests for update command parsing"""

    def test_parse_update_with_playlist(self):
        """Test parsing update command with playlist"""
        parser = setup_parsers()
        args = parser.parse_args(['update', 'My Playlist'])

        assert args.command == 'update'
        assert args.playlist == 'My Playlist'
        assert args.count == 10  # Default
        assert args.fresh_days == 30  # Default
        assert args.score_strategy == 'local'
        assert args.query is None

    def test_parse_update_with_count(self):
        """Test parsing update with --count option"""
        parser = setup_parsers()
        args = parser.parse_args(['update', 'My Playlist', '--count', '20'])

        assert args.count == 20

    def test_parse_update_with_fresh_days(self):
        """Test parsing update with --fresh-days option"""
        parser = setup_parsers()
        args = parser.parse_args(['update', 'My Playlist', '--fresh-days', '7'])

        assert args.fresh_days == 7

    def test_parse_update_all_options(self):
        """Test parsing update with all options"""
        parser = setup_parsers()
        args = parser.parse_args([
            'update', 'My Playlist',
            '--count', '15',
            '--fresh-days', '14'
        ])

        assert args.playlist == 'My Playlist'
        assert args.count == 15
        assert args.fresh_days == 14
        assert args.score_strategy == 'local'
        assert args.query is None

    def test_parse_update_with_scoring_options(self):
        """Test parsing update with scoring options"""
        parser = setup_parsers()
        args = parser.parse_args([
            'update', 'My Playlist',
            '--score-strategy', 'web',
            '--query', 'late night jazz'
        ])

        assert args.score_strategy == 'web'
        assert args.query == 'late night jazz'


class TestStatsCommand:
    """Tests for stats command parsing"""

    def test_parse_stats_no_playlist(self):
        """Test parsing stats without playlist"""
        parser = setup_parsers()
        args = parser.parse_args(['stats'])

        assert args.command == 'stats'
        assert args.playlist is None

    def test_parse_stats_with_playlist(self):
        """Test parsing stats with --playlist option"""
        parser = setup_parsers()
        args = parser.parse_args(['stats', '--playlist', 'My Playlist'])

        assert args.playlist == 'My Playlist'


class TestViewCommand:
    """Tests for view command parsing"""

    def test_parse_view_with_playlist(self):
        """Test parsing view command"""
        parser = setup_parsers()
        args = parser.parse_args(['view', 'My Playlist'])

        assert args.command == 'view'
        assert args.playlist == 'My Playlist'

    def test_parse_view_requires_playlist(self):
        """Test that view requires playlist argument"""
        parser = setup_parsers()

        with pytest.raises(SystemExit):
            parser.parse_args(['view'])


class TestDebugCommand:
    """Tests for debug command parsing"""

    def test_parse_debug_last_default(self):
        parser = setup_parsers()
        args = parser.parse_args(['debug'])

        assert args.command == 'debug'
        assert args.topic == 'last'
        assert args.value is None
        assert args.format == 'json'

    def test_parse_debug_track(self):
        parser = setup_parsers()
        args = parser.parse_args(['debug', 'track', 'artist|||song'])

        assert args.command == 'debug'
        assert args.topic == 'track'
        assert args.value == 'artist|||song'
        assert args.format == 'json'

    def test_parse_debug_with_format(self):
        parser = setup_parsers()
        args = parser.parse_args(['debug', 'last', '--format', 'table'])

        assert args.command == 'debug'
        assert args.topic == 'last'
        assert args.format == 'table'


class TestSyncCommand:
    """Tests for sync command parsing"""

    def test_parse_sync_with_playlist(self):
        """Test parsing sync command"""
        parser = setup_parsers()
        args = parser.parse_args(['sync', 'My Playlist'])

        assert args.command == 'sync'
        assert args.playlist == 'My Playlist'


class TestExtractCommand:
    """Tests for extract command parsing"""

    def test_parse_extract_with_playlist(self):
        """Test parsing extract command"""
        parser = setup_parsers()
        args = parser.parse_args(['extract', 'My Playlist'])

        assert args.command == 'extract'
        assert args.playlist == 'My Playlist'
        assert args.output is None  # Default

    def test_parse_extract_with_output(self):
        """Test parsing extract with --output option"""
        parser = setup_parsers()
        args = parser.parse_args(['extract', 'My Playlist', '--output', 'songs.csv'])

        assert args.output == 'songs.csv'


class TestCleanCommand:
    """Tests for clean command parsing"""

    def test_parse_clean_default(self):
        """Test parsing clean command"""
        parser = setup_parsers()
        args = parser.parse_args(['clean'])

        assert args.command == 'clean'
        assert args.dry_run is False

    def test_parse_clean_dry_run(self):
        """Test parsing clean with --dry-run"""
        parser = setup_parsers()
        args = parser.parse_args(['clean', '--dry-run'])

        assert args.dry_run is True


class TestBackupCommand:
    """Tests for backup command parsing"""

    def test_parse_backup_no_name(self):
        """Test parsing backup without name"""
        parser = setup_parsers()
        args = parser.parse_args(['backup'])

        assert args.command == 'backup'
        assert args.backup_name is None

    def test_parse_backup_with_name(self):
        """Test parsing backup with name"""
        parser = setup_parsers()
        args = parser.parse_args(['backup', 'my_backup'])

        assert args.backup_name == 'my_backup'


class TestRestoreCommand:
    """Tests for restore command parsing"""

    def test_parse_restore_with_name(self):
        """Test parsing restore command"""
        parser = setup_parsers()
        args = parser.parse_args(['restore', 'my_backup'])

        assert args.command == 'restore'
        assert args.backup_name == 'my_backup'

    def test_parse_restore_requires_name(self):
        """Test that restore requires backup_name"""
        parser = setup_parsers()

        with pytest.raises(SystemExit):
            parser.parse_args(['restore'])


class TestRestorePreviousRotationCommand:
    """Tests for restore-previous-rotation command parsing"""

    def test_parse_restore_rotation_default_offset(self):
        """Test parsing with default offset"""
        parser = setup_parsers()
        args = parser.parse_args(['restore-previous-rotation', 'My Playlist'])

        assert args.command == 'restore-previous-rotation'
        assert args.playlist == 'My Playlist'
        assert args.offset == -1  # Default

    def test_parse_restore_rotation_custom_offset(self):
        """Test parsing with custom offset"""
        parser = setup_parsers()
        args = parser.parse_args(['restore-previous-rotation', 'My Playlist', '-5'])

        assert args.offset == -5


class TestListRotationsCommand:
    """Tests for list-rotations command parsing"""

    def test_parse_list_rotations_default(self):
        """Test parsing with default generations"""
        parser = setup_parsers()
        args = parser.parse_args(['list-rotations', 'My Playlist'])

        assert args.command == 'list-rotations'
        assert args.playlist == 'My Playlist'
        assert args.generations == '3'  # Default

    def test_parse_list_rotations_custom_count(self):
        """Test parsing with custom generations count"""
        parser = setup_parsers()
        args = parser.parse_args(['list-rotations', 'My Playlist', '-g', '10'])

        assert args.generations == '10'

    def test_parse_list_rotations_all(self):
        """Test parsing with 'all' generations"""
        parser = setup_parsers()
        args = parser.parse_args(['list-rotations', 'My Playlist', '--generations', 'all'])

        assert args.generations == 'all'


class TestListBackupsCommand:
    """Tests for list-backups command parsing"""

    def test_parse_list_backups(self):
        """Test parsing list-backups command"""
        parser = setup_parsers()
        args = parser.parse_args(['list-backups'])

        assert args.command == 'list-backups'


class TestPlanCommand:
    """Tests for plan command parsing"""

    def test_parse_plan_with_scoring(self):
        parser = setup_parsers()
        args = parser.parse_args([
            'plan', 'My Playlist',
            '--count', '8',
            '--fresh-days', '12',
            '--generations', '4',
            '--score-strategy', 'hybrid',
            '--query', 'ambient focus'
        ])

        assert args.command == 'plan'
        assert args.playlist == 'My Playlist'
        assert args.count == 8
        assert args.fresh_days == 12
        assert args.generations == 4
        assert args.score_strategy == 'hybrid'
        assert args.query == 'ambient focus'


class TestDiffCommand:
    """Tests for diff command parsing"""

    def test_parse_diff_with_scoring(self):
        parser = setup_parsers()
        args = parser.parse_args([
            'diff', 'My Playlist',
            '--count', '6',
            '--fresh-days', '21',
            '--score-strategy', 'web',
            '--query', 'sunny acoustic'
        ])

        assert args.command == 'diff'
        assert args.playlist == 'My Playlist'
        assert args.count == 6
        assert args.fresh_days == 21
        assert args.score_strategy == 'web'
        assert args.query == 'sunny acoustic'


class TestSearchCommand:
    """Tests for search command parsing"""

    def test_parse_search_basic(self):
        parser = setup_parsers()
        args = parser.parse_args(['search', 'late', 'night', 'jazz'])

        assert args.command == 'search'
        assert args.query == ['late', 'night', 'jazz']

    def test_parse_search_requires_query(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(['search'])


class TestIngestCommand:
    """Tests for ingest command parsing"""

    def test_parse_ingest_liked(self):
        parser = setup_parsers()
        args = parser.parse_args(['ingest', 'liked'])
        assert args.command == 'ingest'
        assert args.source == 'liked'
        assert args.name is None
        assert args.time_range == 'medium_term'

    def test_parse_ingest_playlist_with_name(self):
        parser = setup_parsers()
        args = parser.parse_args(['ingest', 'playlist', 'My Playlist'])
        assert args.source == 'playlist'
        assert args.name == 'My Playlist'

    def test_parse_ingest_top_with_time_range(self):
        parser = setup_parsers()
        args = parser.parse_args(['ingest', 'top', '--time-range', 'long_term'])
        assert args.source == 'top'
        assert args.time_range == 'long_term'

    def test_parse_ingest_recent(self):
        parser = setup_parsers()
        args = parser.parse_args(['ingest', 'recent'])
        assert args.source == 'recent'

    def test_parse_ingest_invalid_source(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(['ingest', 'invalid'])

    def test_parse_ingest_invalid_time_range(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(['ingest', 'top', '--time-range', 'invalid'])


class TestListenSyncCommand:
    """Tests for listen-sync command parsing"""

    def test_parse_listen_sync_default(self):
        parser = setup_parsers()
        args = parser.parse_args(['listen-sync'])
        assert args.command == 'listen-sync'
        assert args.limit == 50

    def test_parse_listen_sync_custom_limit(self):
        parser = setup_parsers()
        args = parser.parse_args(['listen-sync', '--limit', '100'])
        assert args.limit == 100


class TestRotateCommand:
    """Tests for rotate command parsing"""

    def test_parse_rotate_default_policy(self):
        parser = setup_parsers()
        args = parser.parse_args(['rotate', 'My Playlist'])
        assert args.command == 'rotate'
        assert args.playlist == 'My Playlist'
        assert args.policy == 'played'
        assert args.max_replace is None

    def test_parse_rotate_with_max_replace(self):
        parser = setup_parsers()
        args = parser.parse_args(['rotate', 'My Playlist', '--max-replace', '5'])
        assert args.max_replace == 5


class TestRotatePlayedCommand:
    """Tests for rotate-played (legacy) command parsing"""

    def test_parse_rotate_played_default(self):
        parser = setup_parsers()
        args = parser.parse_args(['rotate-played', 'My Playlist'])
        assert args.command == 'rotate-played'
        assert args.playlist == 'My Playlist'
        assert args.max_replace is None


class TestAuthCommands:
    """Tests for auth-status and auth-refresh command parsing"""

    def test_parse_auth_status(self):
        parser = setup_parsers()
        args = parser.parse_args(['auth-status'])
        assert args.command == 'auth-status'

    def test_parse_auth_refresh(self):
        parser = setup_parsers()
        args = parser.parse_args(['auth-refresh'])
        assert args.command == 'auth-refresh'


class TestInteractiveCommand:
    """Tests for interactive command parsing"""

    def test_parse_interactive(self):
        parser = setup_parsers()
        args = parser.parse_args(['interactive'])
        assert args.command == 'interactive'


class TestParseArgsFunction:
    """Tests for the parse_args function"""

    def test_parse_args_returns_command_and_args(self):
        """Test that parse_args returns tuple of command and args"""
        with patch.object(sys, 'argv', ['cli', 'stats']):
            command, args = parse_args()

            assert command == 'stats'
            assert args is not None

    def test_parse_args_no_command(self):
        """Test parse_args with no command"""
        with patch.object(sys, 'argv', ['cli']):
            # Capture stdout to suppress help message
            with patch('sys.stdout'):
                command, args = parse_args()

            assert command is None
            assert args is None


class TestParseTokensFunction:
    """Tests for interactive token parsing"""

    def test_parse_tokens_valid(self):
        command, args, error = parse_tokens(['stats'])
        assert command == 'stats'
        assert error is None

    def test_parse_tokens_missing_required(self):
        command, args, error = parse_tokens(['update'])
        assert command is None
        assert error is not None

    def test_parse_tokens_empty(self):
        command, args, error = parse_tokens([])
        assert command is None
        assert error is not None

    def test_parse_tokens_valid_with_args(self):
        command, args, error = parse_tokens(['update', 'My Playlist', '--count', '5'])
        assert command == 'update'
        assert args.playlist == 'My Playlist'
        assert args.count == 5
        assert error is None

    def test_parse_tokens_invalid_command(self):
        command, args, error = parse_tokens(['nonexistent'])
        assert command is None
        assert error is not None

    def test_parse_tokens_all_commands(self):
        """Verify parse_tokens handles every registered command."""
        test_cases = [
            (['stats'], 'stats'),
            (['view', 'PL'], 'view'),
            (['backup'], 'backup'),
            (['list-backups'], 'list-backups'),
            (['auth-status'], 'auth-status'),
            (['auth-refresh'], 'auth-refresh'),
            (['search', 'jazz'], 'search'),
            (['clean'], 'clean'),
            (['interactive'], 'interactive'),
        ]
        for tokens, expected_cmd in test_cases:
            command, args, error = parse_tokens(tokens)
            assert command == expected_cmd, f"Failed for tokens {tokens}: got {command}"
            assert error is None, f"Unexpected error for {tokens}: {error}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
