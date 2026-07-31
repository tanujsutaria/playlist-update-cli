"""
Unit tests for argument parsing.
Tests all CLI commands and their argument handling.
"""

import sys
from unittest.mock import patch

import pytest

from arg_parse import HelpText, parse_args, parse_tokens, setup_parsers, unknown_command_message


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
            "import",
            "update",
            "stats",
            "view",
            "sync",
            "extract",
            "clean",
            "backup",
            "restore",
            "restore-previous-rotation",
            "list-rotations",
            "list-backups",
            "plan",
            "diff",
            "auth-status",
            "auth-refresh",
            "auth-reset",
            "search",
            "find",
            "undo",
            "enrich",
            "sonic",
            "debug",
            "interactive",
            "ingest",
            "listen-sync",
            "pull",
            "rotate",
            "rotate-played",
            "doctor",
            "embed",
            "similar",
            "add",
            "remove",
            "move",
        ]

        # The _subparsers action contains the choices
        for action in parser._actions:
            if hasattr(action, "choices") and action.choices:
                for cmd in expected_commands:
                    assert cmd in action.choices, f"Command '{cmd}' not found in parser"


class TestAuthResetCommand:
    """Tests for auth-reset command parsing"""

    def test_parse_auth_reset_defaults_unconfirmed(self):
        """Bare auth-reset parses with yes=False (nothing gets deleted)."""
        parser = setup_parsers()
        args = parser.parse_args(["auth-reset"])
        assert args.command == "auth-reset"
        assert args.yes is False

    def test_parse_auth_reset_with_yes(self):
        parser = setup_parsers()
        args = parser.parse_args(["auth-reset", "--yes"])
        assert args.command == "auth-reset"
        assert args.yes is True


class TestImportCommand:
    """Tests for import command parsing"""

    def test_parse_import_with_file(self):
        """Test parsing import command with file argument"""
        parser = setup_parsers()
        args = parser.parse_args(["import", "songs.csv"])

        assert args.command == "import"
        assert args.file == "songs.csv"

    def test_parse_import_requires_file(self):
        """Test that import requires file argument"""
        parser = setup_parsers()

        with pytest.raises(SystemExit):
            parser.parse_args(["import"])


class TestUpdateCommand:
    """Tests for update command parsing"""

    def test_parse_update_with_playlist(self):
        """Test parsing update command with playlist"""
        parser = setup_parsers()
        args = parser.parse_args(["update", "My Playlist"])

        assert args.command == "update"
        assert args.playlist == "My Playlist"
        assert args.count == 10  # Default
        assert args.fresh_days == 30  # Default
        assert args.score_strategy == "local"
        assert args.query is None

    def test_parse_update_with_count(self):
        """Test parsing update with --count option"""
        parser = setup_parsers()
        args = parser.parse_args(["update", "My Playlist", "--count", "20"])

        assert args.count == 20

    def test_parse_update_with_fresh_days(self):
        """Test parsing update with --fresh-days option"""
        parser = setup_parsers()
        args = parser.parse_args(["update", "My Playlist", "--fresh-days", "7"])

        assert args.fresh_days == 7

    def test_parse_update_all_options(self):
        """Test parsing update with all options"""
        parser = setup_parsers()
        args = parser.parse_args(["update", "My Playlist", "--count", "15", "--fresh-days", "14"])

        assert args.playlist == "My Playlist"
        assert args.count == 15
        assert args.fresh_days == 14
        assert args.score_strategy == "local"
        assert args.query is None

    def test_parse_update_with_scoring_options(self):
        """Test parsing update with scoring options"""
        parser = setup_parsers()
        args = parser.parse_args(
            ["update", "My Playlist", "--score-strategy", "web", "--query", "late night jazz"]
        )

        assert args.score_strategy == "web"
        assert args.query == "late night jazz"


class TestStatsCommand:
    """Tests for stats command parsing"""

    def test_parse_stats_no_playlist(self):
        """Test parsing stats without playlist"""
        parser = setup_parsers()
        args = parser.parse_args(["stats"])

        assert args.command == "stats"
        assert args.playlist is None

    def test_parse_stats_with_playlist(self):
        """Test parsing stats with --playlist option"""
        parser = setup_parsers()
        args = parser.parse_args(["stats", "--playlist", "My Playlist"])

        assert args.playlist == "My Playlist"


class TestViewCommand:
    """Tests for view command parsing"""

    def test_parse_view_with_playlist(self):
        """Test parsing view command"""
        parser = setup_parsers()
        args = parser.parse_args(["view", "My Playlist"])

        assert args.command == "view"
        assert args.playlist == "My Playlist"

    def test_parse_view_requires_playlist(self):
        """Test that view requires playlist argument"""
        parser = setup_parsers()

        with pytest.raises(SystemExit):
            parser.parse_args(["view"])


class TestDebugCommand:
    """Tests for debug command parsing"""

    def test_parse_debug_last_default(self):
        parser = setup_parsers()
        args = parser.parse_args(["debug"])

        assert args.command == "debug"
        assert args.topic == "last"
        assert args.value is None
        assert args.format == "json"

    def test_parse_debug_track(self):
        parser = setup_parsers()
        args = parser.parse_args(["debug", "track", "artist|||song"])

        assert args.command == "debug"
        assert args.topic == "track"
        assert args.value == "artist|||song"
        assert args.format == "json"

    def test_parse_debug_with_format(self):
        parser = setup_parsers()
        args = parser.parse_args(["debug", "last", "--format", "table"])

        assert args.command == "debug"
        assert args.topic == "last"
        assert args.format == "table"


class TestSyncCommand:
    """Tests for sync command parsing"""

    def test_parse_sync_with_playlist(self):
        """Test parsing sync command"""
        parser = setup_parsers()
        args = parser.parse_args(["sync", "My Playlist"])

        assert args.command == "sync"
        assert args.playlist == "My Playlist"


class TestExtractCommand:
    """Tests for extract command parsing"""

    def test_parse_extract_with_playlist(self):
        """Test parsing extract command"""
        parser = setup_parsers()
        args = parser.parse_args(["extract", "My Playlist"])

        assert args.command == "extract"
        assert args.playlist == "My Playlist"
        assert args.output is None  # Default

    def test_parse_extract_with_output(self):
        """Test parsing extract with --output option"""
        parser = setup_parsers()
        args = parser.parse_args(["extract", "My Playlist", "--output", "songs.csv"])

        assert args.output == "songs.csv"


class TestCleanCommand:
    """Tests for clean command parsing"""

    def test_parse_clean_default(self):
        """Test parsing clean command"""
        parser = setup_parsers()
        args = parser.parse_args(["clean"])

        assert args.command == "clean"
        assert args.dry_run is False

    def test_parse_clean_dry_run(self):
        """Test parsing clean with --dry-run"""
        parser = setup_parsers()
        args = parser.parse_args(["clean", "--dry-run"])

        assert args.dry_run is True


class TestBackupCommand:
    """Tests for backup command parsing"""

    def test_parse_backup_no_name(self):
        """Test parsing backup without name"""
        parser = setup_parsers()
        args = parser.parse_args(["backup"])

        assert args.command == "backup"
        assert args.backup_name is None

    def test_parse_backup_with_name(self):
        """Test parsing backup with name"""
        parser = setup_parsers()
        args = parser.parse_args(["backup", "my_backup"])

        assert args.backup_name == "my_backup"


class TestRestoreCommand:
    """Tests for restore command parsing"""

    def test_parse_restore_with_name(self):
        """Test parsing restore command"""
        parser = setup_parsers()
        args = parser.parse_args(["restore", "my_backup"])

        assert args.command == "restore"
        assert args.backup_name == "my_backup"

    def test_parse_restore_requires_name(self):
        """Test that restore requires backup_name"""
        parser = setup_parsers()

        with pytest.raises(SystemExit):
            parser.parse_args(["restore"])


class TestRestorePreviousRotationCommand:
    """Tests for restore-previous-rotation command parsing"""

    def test_parse_restore_rotation_default_offset(self):
        """Test parsing with default offset"""
        parser = setup_parsers()
        args = parser.parse_args(["restore-previous-rotation", "My Playlist"])

        assert args.command == "restore-previous-rotation"
        assert args.playlist == "My Playlist"
        assert args.offset == -1  # Default

    def test_parse_restore_rotation_custom_offset(self):
        """Test parsing with custom offset"""
        parser = setup_parsers()
        args = parser.parse_args(["restore-previous-rotation", "My Playlist", "-5"])

        assert args.offset == -5


class TestListRotationsCommand:
    """Tests for list-rotations command parsing"""

    def test_parse_list_rotations_default(self):
        """Test parsing with default generations"""
        parser = setup_parsers()
        args = parser.parse_args(["list-rotations", "My Playlist"])

        assert args.command == "list-rotations"
        assert args.playlist == "My Playlist"
        assert args.generations == "3"  # Default

    def test_parse_list_rotations_custom_count(self):
        """Test parsing with custom generations count"""
        parser = setup_parsers()
        args = parser.parse_args(["list-rotations", "My Playlist", "-g", "10"])

        assert args.generations == "10"

    def test_parse_list_rotations_all(self):
        """Test parsing with 'all' generations"""
        parser = setup_parsers()
        args = parser.parse_args(["list-rotations", "My Playlist", "--generations", "all"])

        assert args.generations == "all"


class TestListBackupsCommand:
    """Tests for list-backups command parsing"""

    def test_parse_list_backups(self):
        """Test parsing list-backups command"""
        parser = setup_parsers()
        args = parser.parse_args(["list-backups"])

        assert args.command == "list-backups"


class TestPlanCommand:
    """Tests for plan command parsing"""

    def test_parse_plan_with_scoring(self):
        parser = setup_parsers()
        args = parser.parse_args(
            [
                "plan",
                "My Playlist",
                "--count",
                "8",
                "--fresh-days",
                "12",
                "--generations",
                "4",
                "--score-strategy",
                "hybrid",
                "--query",
                "ambient focus",
            ]
        )

        assert args.command == "plan"
        assert args.playlist == "My Playlist"
        assert args.count == 8
        assert args.fresh_days == 12
        assert args.generations == 4
        assert args.score_strategy == "hybrid"
        assert args.query == "ambient focus"


class TestDiffCommand:
    """Tests for diff command parsing"""

    def test_parse_diff_with_scoring(self):
        parser = setup_parsers()
        args = parser.parse_args(
            [
                "diff",
                "My Playlist",
                "--count",
                "6",
                "--fresh-days",
                "21",
                "--score-strategy",
                "web",
                "--query",
                "sunny acoustic",
            ]
        )

        assert args.command == "diff"
        assert args.playlist == "My Playlist"
        assert args.count == 6
        assert args.fresh_days == 21
        assert args.score_strategy == "web"
        assert args.query == "sunny acoustic"


class TestSearchCommand:
    """Tests for search command parsing"""

    def test_parse_search_basic(self):
        parser = setup_parsers()
        args = parser.parse_args(["search", "late", "night", "jazz"])

        assert args.command == "search"
        assert args.query == ["late", "night", "jazz"]

    def test_parse_search_requires_query(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["search"])

    def test_search_write_flags_default_off(self):
        parser = setup_parsers()
        args = parser.parse_args(["search", "late", "night", "jazz"])
        assert args.to_playlist is None
        assert args.replace is False
        assert args.save is False
        assert args.limit is None

    def test_search_to_playlist(self):
        parser = setup_parsers()
        args = parser.parse_args(["search", "jazz", "--to", "My Mix"])
        assert args.query == ["jazz"]
        assert args.to_playlist == "My Mix"
        assert args.replace is False

    def test_search_replace_save_and_limit(self):
        parser = setup_parsers()
        args = parser.parse_args(
            ["search", "jazz", "--to", "My Mix", "--replace", "--save", "--limit", "5"]
        )
        assert args.to_playlist == "My Mix"
        assert args.replace is True
        assert args.save is True
        assert args.limit == 5

    def test_search_save_without_to(self):
        parser = setup_parsers()
        args = parser.parse_args(["search", "jazz", "--save"])
        assert args.save is True
        assert args.to_playlist is None

    def test_search_json_flag(self):
        parser = setup_parsers()
        assert parser.parse_args(["search", "jazz"]).json is False
        assert parser.parse_args(["search", "jazz", "--json"]).json is True


class TestFindCommand:
    """/find: deep search re-ranked by taste, with optional write flags."""

    def test_parse_find_defaults(self):
        parser = setup_parsers()
        args = parser.parse_args(["find", "late", "night", "jazz"])
        assert args.command == "find"
        assert args.query == ["late", "night", "jazz"]
        assert args.taste_weight == 0.5
        assert args.to_playlist is None
        assert args.replace is False
        assert args.limit is None
        assert args.json is False

    def test_parse_find_all_flags(self):
        parser = setup_parsers()
        args = parser.parse_args(
            [
                "find",
                "jazz",
                "--taste-weight",
                "0.8",
                "--to",
                "My Mix",
                "--replace",
                "--limit",
                "10",
            ]
        )
        assert args.taste_weight == 0.8
        assert args.to_playlist == "My Mix"
        assert args.replace is True
        assert args.limit == 10

    def test_parse_find_requires_query(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["find"])


class TestUndoCommand:
    """The /undo command takes no arguments."""

    def test_parse_undo(self):
        parser = setup_parsers()
        args = parser.parse_args(["undo"])
        assert args.command == "undo"


class TestEnrichCommand:
    """/enrich backfills semantic context; bounded by --limit, with --dry-run."""

    def test_parse_enrich_defaults(self):
        parser = setup_parsers()
        args = parser.parse_args(["enrich"])
        assert args.command == "enrich"
        assert args.limit == 25
        assert args.dry_run is False
        assert args.concurrency == 8
        # No cohort flag = whole-library (today's behavior).
        assert args.played is False
        assert args.liked is False
        assert args.rotation is False
        assert args.playlist is None

    def test_parse_enrich_flags(self):
        parser = setup_parsers()
        args = parser.parse_args(["enrich", "--limit", "100", "--dry-run", "--concurrency", "16"])
        assert args.limit == 100
        assert args.dry_run is True
        assert args.concurrency == 16

    def test_parse_enrich_cohort_flags(self):
        parser = setup_parsers()
        assert parser.parse_args(["enrich", "--played"]).played is True
        assert parser.parse_args(["enrich", "--liked"]).liked is True
        assert parser.parse_args(["enrich", "--rotation"]).rotation is True
        assert parser.parse_args(["enrich", "--playlist", "My Mix"]).playlist == "My Mix"

    def test_parse_enrich_cohorts_mutually_exclusive(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["enrich", "--played", "--liked"])
        with pytest.raises(SystemExit):
            parser.parse_args(["enrich", "--rotation", "--playlist", "My Mix"])

    def test_parse_enrich_playlist_requires_name(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["enrich", "--playlist"])

    def test_parse_enrich_cohort_keeps_limit_default(self):
        # Cost discipline: a cohort flag must NOT loosen the --limit default.
        parser = setup_parsers()
        assert parser.parse_args(["enrich", "--liked"]).limit == 25


class TestSonicCommand:
    """/sonic backfills acoustic features; bounded by --limit, with --dry-run."""

    def test_parse_sonic_defaults(self):
        parser = setup_parsers()
        args = parser.parse_args(["sonic"])
        assert args.command == "sonic"
        assert args.limit == 50
        assert args.dry_run is False
        # No cohort flag = whole-library (today's behavior).
        assert args.played is False
        assert args.liked is False
        assert args.rotation is False
        assert args.playlist is None

    def test_parse_sonic_flags(self):
        parser = setup_parsers()
        args = parser.parse_args(["sonic", "--limit", "200", "--dry-run"])
        assert args.limit == 200
        assert args.dry_run is True

    def test_parse_sonic_cohort_flags(self):
        parser = setup_parsers()
        assert parser.parse_args(["sonic", "--played"]).played is True
        assert parser.parse_args(["sonic", "--liked"]).liked is True
        assert parser.parse_args(["sonic", "--rotation"]).rotation is True
        assert parser.parse_args(["sonic", "--playlist", "My Mix"]).playlist == "My Mix"

    def test_parse_sonic_cohorts_mutually_exclusive(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["sonic", "--played", "--liked"])
        with pytest.raises(SystemExit):
            parser.parse_args(["sonic", "--liked", "--playlist", "My Mix"])

    def test_parse_sonic_playlist_requires_name(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["sonic", "--playlist"])


class TestIngestCommand:
    """Tests for ingest command parsing"""

    def test_parse_ingest_liked(self):
        parser = setup_parsers()
        args = parser.parse_args(["ingest", "liked"])
        assert args.command == "ingest"
        assert args.source == "liked"
        assert args.name is None
        assert args.time_range == "medium_term"

    def test_parse_ingest_playlist_with_name(self):
        parser = setup_parsers()
        args = parser.parse_args(["ingest", "playlist", "My Playlist"])
        assert args.source == "playlist"
        assert args.name == "My Playlist"

    def test_parse_ingest_top_with_time_range(self):
        parser = setup_parsers()
        args = parser.parse_args(["ingest", "top", "--time-range", "long_term"])
        assert args.source == "top"
        assert args.time_range == "long_term"

    def test_parse_ingest_recent(self):
        parser = setup_parsers()
        args = parser.parse_args(["ingest", "recent"])
        assert args.source == "recent"

    def test_parse_ingest_invalid_source(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["ingest", "invalid"])

    def test_parse_ingest_invalid_time_range(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["ingest", "top", "--time-range", "invalid"])


class TestListenSyncCommand:
    """Tests for listen-sync command parsing"""

    def test_parse_listen_sync_default(self):
        parser = setup_parsers()
        args = parser.parse_args(["listen-sync"])
        assert args.command == "listen-sync"
        assert args.limit == 50

    def test_parse_listen_sync_custom_limit(self):
        parser = setup_parsers()
        args = parser.parse_args(["listen-sync", "--limit", "100"])
        assert args.limit == 100


class TestRotateCommand:
    """Tests for rotate command parsing"""

    def test_parse_rotate_default(self):
        parser = setup_parsers()
        args = parser.parse_args(["rotate", "My Playlist"])
        assert args.command == "rotate"
        assert args.playlist == "My Playlist"
        assert args.max_replace is None
        # `--policy` was removed (it was a single-value, redundant flag).
        assert not hasattr(args, "policy")

    def test_parse_rotate_with_max_replace(self):
        parser = setup_parsers()
        args = parser.parse_args(["rotate", "My Playlist", "--max-replace", "5"])
        assert args.max_replace == 5

    def test_parse_rotate_dry_run_defaults_false(self):
        parser = setup_parsers()
        args = parser.parse_args(["rotate", "My Playlist"])
        assert args.dry_run is False

    def test_parse_rotate_with_dry_run(self):
        parser = setup_parsers()
        args = parser.parse_args(["rotate", "My Playlist", "--dry-run"])
        assert args.dry_run is True


class TestRotatePlayedCommand:
    """Tests for rotate-played (legacy) command parsing"""

    def test_parse_rotate_played_default(self):
        parser = setup_parsers()
        args = parser.parse_args(["rotate-played", "My Playlist"])
        assert args.command == "rotate-played"
        assert args.playlist == "My Playlist"
        assert args.max_replace is None


class TestProfileCommand:
    """Tests for profile command parsing"""

    def test_parse_profile_default_top(self):
        parser = setup_parsers()
        args = parser.parse_args(["profile"])
        assert args.command == "profile"
        assert args.top == 15

    def test_parse_profile_custom_top(self):
        parser = setup_parsers()
        args = parser.parse_args(["profile", "--top", "5"])
        assert args.top == 5

    def test_parse_profile_json(self):
        parser = setup_parsers()
        assert parser.parse_args(["profile"]).json is False
        assert parser.parse_args(["profile", "--json"]).json is True


class TestTasteCommand:
    """Tests for taste command parsing"""

    def test_parse_taste_default_top(self):
        parser = setup_parsers()
        args = parser.parse_args(["taste"])
        assert args.command == "taste"
        assert args.top == 8

    def test_parse_taste_custom_top(self):
        parser = setup_parsers()
        args = parser.parse_args(["taste", "--top", "3"])
        assert args.top == 3

    def test_parse_taste_json(self):
        parser = setup_parsers()
        assert parser.parse_args(["taste"]).json is False
        assert parser.parse_args(["taste", "--json"]).json is True


class TestAuthCommands:
    """Tests for auth-status and auth-refresh command parsing"""

    def test_parse_auth_status(self):
        parser = setup_parsers()
        args = parser.parse_args(["auth-status"])
        assert args.command == "auth-status"

    def test_parse_auth_refresh(self):
        parser = setup_parsers()
        args = parser.parse_args(["auth-refresh"])
        assert args.command == "auth-refresh"


class TestInteractiveCommand:
    """Tests for interactive command parsing"""

    def test_parse_interactive(self):
        parser = setup_parsers()
        args = parser.parse_args(["interactive"])
        assert args.command == "interactive"


class TestDoctorCommand:
    """/doctor: offline integrity audit, no arguments beyond --json."""

    def test_parse_doctor_default(self):
        parser = setup_parsers()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"
        assert args.json is False

    def test_parse_doctor_json(self):
        parser = setup_parsers()
        assert parser.parse_args(["doctor", "--json"]).json is True


class TestParseArgsFunction:
    """Tests for the parse_args function"""

    def test_parse_args_returns_command_and_args(self):
        """Test that parse_args returns tuple of command and args"""
        with patch.object(sys, "argv", ["cli", "stats"]):
            command, args = parse_args()

            assert command == "stats"
            assert args is not None

    def test_parse_args_no_command(self):
        """Test parse_args with no command"""
        with patch.object(sys, "argv", ["cli"]):
            # Capture stdout to suppress help message
            with patch("sys.stdout"):
                command, args = parse_args()

            assert command is None
            assert args is None


class TestParseTokensFunction:
    """Tests for interactive token parsing"""

    def test_parse_tokens_valid(self):
        command, args, error = parse_tokens(["stats"])
        assert command == "stats"
        assert error is None

    def test_parse_tokens_missing_required(self):
        command, args, error = parse_tokens(["update"])
        assert command is None
        assert error is not None

    def test_parse_tokens_empty(self):
        command, args, error = parse_tokens([])
        assert command is None
        assert error is not None

    def test_parse_tokens_valid_with_args(self):
        command, args, error = parse_tokens(["update", "My Playlist", "--count", "5"])
        assert command == "update"
        assert args.playlist == "My Playlist"
        assert args.count == 5
        assert error is None

    def test_parse_tokens_invalid_command(self):
        command, args, error = parse_tokens(["nonexistent"])
        assert command is None
        assert error is not None

    def test_parse_tokens_subcommand_help_flag(self):
        """`/update --help` yields HelpText carrying the subcommand's flags."""
        command, args, error = parse_tokens(["update", "--help"])
        assert command is None
        assert args is None
        assert isinstance(error, HelpText)
        # Assert flag substrings, never the usage prefix (prog renders
        # differently under `python -c`).
        assert "--count" in error
        assert "--fresh-days" in error

    def test_parse_tokens_subcommand_short_help_flag(self):
        command, args, error = parse_tokens(["update", "-h"])
        assert isinstance(error, HelpText)
        assert "--count" in error

    def test_parse_tokens_top_level_help_flag(self):
        command, args, error = parse_tokens(["--help"])
        assert isinstance(error, HelpText)
        assert "update" in error

    def test_parse_tokens_missing_arg_error_carries_usage(self):
        command, args, error = parse_tokens(["update"])
        assert command is None
        assert error is not None
        assert not isinstance(error, HelpText)
        assert "usage:" in error
        assert "playlist" in error

    def test_parse_tokens_did_you_mean_typo(self):
        command, args, error = parse_tokens(["serch", "x"])
        assert command is None
        assert error is not None
        assert not isinstance(error, HelpText)
        assert "/search" in error
        assert "/help" in error

    def test_parse_tokens_unknown_without_close_match(self):
        command, args, error = parse_tokens(["zzqqx"])
        assert error is not None
        assert "Unknown command /zzqqx." in error
        assert "Did you mean" not in error
        assert "/help" in error

    def test_parse_tokens_extra_commands_extend_suggestions(self):
        command, args, error = parse_tokens(["clera"], extra_commands=["clear", "cls"])
        assert error is not None
        assert "/clear" in error

    def test_parse_tokens_does_not_suggest_hidden_commands(self):
        """Deprecated/hidden subcommands never surface as suggestions."""
        command, args, error = parse_tokens(["rotate-playd"])
        assert error is not None
        assert "rotate-played" not in error

    def test_unknown_command_message_helper(self):
        message = unknown_command_message("serch", ["search", "stats"])
        assert message == "Unknown command /serch. Did you mean /search? Type /help for the list."

    def test_parse_tokens_all_commands(self):
        """Verify parse_tokens handles every registered command."""
        test_cases = [
            (["stats"], "stats"),
            (["view", "PL"], "view"),
            (["backup"], "backup"),
            (["list-backups"], "list-backups"),
            (["auth-status"], "auth-status"),
            (["auth-refresh"], "auth-refresh"),
            (["search", "jazz"], "search"),
            (["clean"], "clean"),
            (["interactive"], "interactive"),
        ]
        for tokens, expected_cmd in test_cases:
            command, args, error = parse_tokens(tokens)
            assert command == expected_cmd, f"Failed for tokens {tokens}: got {command}"
            assert error is None, f"Unexpected error for {tokens}: {error}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestUnknownCommandSelfReference:
    """A meta command typed with arguments must not suggest itself."""

    def test_meta_command_with_args_is_not_its_own_suggestion(self):
        message = unknown_command_message("clear", ["clear", "search"])
        assert message == "/clear doesn't take those arguments. Try /help clear."

    def test_close_match_still_suggested(self):
        message = unknown_command_message("serch", ["search", "clear"])
        assert "Did you mean /search?" in message


class TestProgName:
    """Usage lines must read "tunr ...", not the module name argparse infers."""

    def test_parser_prog_is_tunr(self):
        parser = setup_parsers()
        assert parser.prog == "tunr"

    def test_subparser_usage_carries_tunr(self):
        parser = setup_parsers(exit_on_error=False)
        sub = None
        for action in parser._actions:
            if hasattr(action, "choices") and action.choices and "update" in action.choices:
                sub = action.choices["update"]
                break
        assert sub is not None
        assert "tunr update" in sub.format_usage()


class TestFlagDidYouMean:
    """Mistyped flags get a difflib did-you-mean, detected via parse_known_args
    extras (never by string-matching argparse wording)."""

    def test_update_mistyped_count(self):
        command, args, error = parse_tokens(["update", "My Playlist", "--cout", "5"])
        assert command is None
        assert error is not None
        assert "Unrecognized --cout — did you mean --count?" in error

    def test_stats_mistyped_playlist(self):
        command, args, error = parse_tokens(["stats", "--playlst", "X"])
        assert command is None
        assert error is not None
        assert "Unrecognized --playlst — did you mean --playlist?" in error

    def test_equals_form_suggests_bare_flag(self):
        command, args, error = parse_tokens(["update", "My Playlist", "--cout=5"])
        assert error is not None
        assert "did you mean --count?" in error

    def test_unrelated_flag_gets_no_suggestion(self):
        command, args, error = parse_tokens(["update", "My Playlist", "--zzzzqqq"])
        assert command is None
        assert error is not None
        assert "did you mean" not in error

    def test_missing_required_arg_has_no_flag_hint(self):
        # parse_known_args raises here too — no extras, no hint, and the
        # original usage-carrying error is preserved.
        command, args, error = parse_tokens(["update"])
        assert error is not None
        assert "did you mean" not in error
        assert "usage: tunr update" in error

    def test_valid_flags_still_parse(self):
        command, args, error = parse_tokens(["update", "My Playlist", "--count", "5"])
        assert error is None
        assert command == "update"
        assert args.count == 5


class TestEmbedCommand:
    """Tests for embed command parsing"""

    def test_parse_embed_defaults(self):
        """Bare embed parses with no limit (all missing) and no dry-run."""
        parser = setup_parsers()
        args = parser.parse_args(["embed"])
        assert args.command == "embed"
        assert args.limit is None
        assert args.dry_run is False

    def test_parse_embed_with_flags(self):
        parser = setup_parsers()
        args = parser.parse_args(["embed", "--limit", "500", "--dry-run"])
        assert args.limit == 500
        assert args.dry_run is True

    def test_parse_embed_rejects_non_positive_limit(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["embed", "--limit", "0"])


class TestSimilarCommand:
    """Tests for similar command parsing"""

    def test_parse_similar_defaults(self):
        parser = setup_parsers()
        args = parser.parse_args(["similar", "artist|||song"])
        assert args.command == "similar"
        assert args.query == ["artist|||song"]
        assert args.limit == 10
        assert args.to_playlist is None
        assert args.json is False

    def test_parse_similar_free_text_and_flags(self):
        parser = setup_parsers()
        args = parser.parse_args(
            ["similar", "late", "night", "jazz", "--limit", "5", "--to", "My Mix", "--json"]
        )
        assert args.query == ["late", "night", "jazz"]
        assert args.limit == 5
        assert args.to_playlist == "My Mix"
        assert args.json is True

    def test_parse_similar_requires_query(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["similar"])
class TestAddCommand:
    """Tests for add command parsing (quick track ops)"""

    def test_parse_add_with_query_and_to(self):
        parser = setup_parsers()
        args = parser.parse_args(["add", "wild", "nothing", "-", "shadow", "--to", "My Mix"])

        assert args.command == "add"
        assert args.query == ["wild", "nothing", "-", "shadow"]
        assert args.to_playlist == "My Mix"
        assert args.track_id is None

    def test_parse_add_requires_to(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["add", "some", "song"])

    def test_parse_add_with_id_bypass(self):
        """--id needs no positional query (exact bypass)."""
        parser = setup_parsers()
        args = parser.parse_args(["add", "--id", "artist|||song", "--to", "Mix"])

        assert args.query == []
        assert args.track_id == "artist|||song"
        assert args.to_playlist == "Mix"


class TestRemoveCommand:
    """Tests for remove command parsing (quick track ops)"""

    def test_parse_remove_with_query_and_from(self):
        parser = setup_parsers()
        args = parser.parse_args(["remove", "shadow", "--from", "My Mix"])

        assert args.command == "remove"
        assert args.query == ["shadow"]
        assert args.from_playlist == "My Mix"
        assert args.track_id is None

    def test_parse_remove_requires_from(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["remove", "shadow"])

    def test_parse_remove_with_id_bypass(self):
        parser = setup_parsers()
        args = parser.parse_args(["remove", "--id", "artist|||song", "--from", "Mix"])

        assert args.query == []
        assert args.track_id == "artist|||song"
        assert args.from_playlist == "Mix"

    def test_remove_help_documents_all_occurrences(self):
        """The help/description must warn that ALL duplicate occurrences vanish."""
        parser = setup_parsers()
        sub = None
        for action in parser._actions:
            if hasattr(action, "choices") and action.choices and "remove" in action.choices:
                sub = action.choices["remove"]
                break
        assert sub is not None
        assert "playlist_remove_all_occurrences_of_items" in sub.format_help()


class TestMoveCommand:
    """Tests for move command parsing (quick track ops)"""

    def test_parse_move_with_from_and_to(self):
        parser = setup_parsers()
        args = parser.parse_args(["move", "shadow", "--from", "My Mix", "--to", "Chill"])

        assert args.command == "move"
        assert args.query == ["shadow"]
        assert args.from_playlist == "My Mix"
        assert args.to_playlist == "Chill"
        assert args.track_id is None

    def test_parse_move_requires_from(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["move", "shadow", "--to", "Chill"])

    def test_parse_move_requires_to(self):
        parser = setup_parsers()
        with pytest.raises(SystemExit):
            parser.parse_args(["move", "shadow", "--from", "My Mix"])

    def test_parse_move_with_id_bypass(self):
        parser = setup_parsers()
        args = parser.parse_args(["move", "--id", "a|||b", "--from", "Mix", "--to", "Chill"])

        assert args.query == []
        assert args.track_id == "a|||b"
        assert args.from_playlist == "Mix"
        assert args.to_playlist == "Chill"
