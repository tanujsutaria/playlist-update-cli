"""
Unit tests for dispatch_command routing.
Verifies that every CLI command routes to the correct PlaylistCLI method.
"""
import argparse
from unittest.mock import MagicMock, patch, call

import pytest

from main import PlaylistCLI, dispatch_command


@pytest.fixture
def cli():
    """Bare PlaylistCLI with all public methods mocked."""
    c = PlaylistCLI.__new__(PlaylistCLI)
    c._db = MagicMock()
    c._spotify = MagicMock()
    c._rotation_managers = {}
    c._storage = None
    c._repos = None
    c._search_pipeline = None
    c.last_search_results = None
    c.last_search_query = None
    c.last_search_summary = None
    c.last_search_metrics = None
    c.last_search_constraints = None
    c.last_search_expanded = False
    c.last_search_policy = None
    c.last_search_run_id = None
    c.last_search_track_ids = None
    c.last_search_cached = False
    return c


def _make_args(**kwargs):
    return argparse.Namespace(**kwargs)


# ---- import ----
class TestDispatchImport:
    def test_import_routes_correctly(self, cli):
        cli.import_songs = MagicMock()
        args = _make_args(file="songs.csv")
        rc = dispatch_command(cli, "import", args)
        assert rc == 0
        cli.import_songs.assert_called_once_with("songs.csv")


# ---- update ----
class TestDispatchUpdate:
    def test_update_routes_correctly(self, cli):
        cli.update_playlist = MagicMock()
        args = _make_args(
            playlist="My Playlist", count=10, fresh_days=30,
            dry_run=False, score_strategy="local", query=None,
        )
        rc = dispatch_command(cli, "update", args)
        assert rc == 0
        cli.update_playlist.assert_called_once_with(
            "My Playlist", 10, 30, False, "local", None,
        )


# ---- stats ----
class TestDispatchStats:
    def test_stats_without_export(self, cli):
        cli.show_stats = MagicMock()
        args = _make_args(playlist="PL", export=None, output=None)
        rc = dispatch_command(cli, "stats", args)
        assert rc == 0
        cli.show_stats.assert_called_once_with("PL")

    def test_stats_with_export(self, cli):
        cli.export_stats = MagicMock()
        args = _make_args(playlist="PL", export="json", output="out.json")
        rc = dispatch_command(cli, "stats", args)
        assert rc == 0
        cli.export_stats.assert_called_once_with("PL", "json", "out.json")


# ---- view ----
class TestDispatchView:
    def test_view_routes_correctly(self, cli):
        cli.view_playlist = MagicMock()
        args = _make_args(playlist="PL")
        rc = dispatch_command(cli, "view", args)
        assert rc == 0
        cli.view_playlist.assert_called_once_with("PL")


# ---- sync ----
class TestDispatchSync:
    def test_sync_routes_correctly(self, cli):
        cli.sync_playlist = MagicMock()
        args = _make_args(playlist="PL")
        rc = dispatch_command(cli, "sync", args)
        assert rc == 0
        cli.sync_playlist.assert_called_once_with("PL")


# ---- extract ----
class TestDispatchExtract:
    def test_extract_routes_correctly(self, cli):
        cli.extract_playlist = MagicMock()
        args = _make_args(playlist="PL", output="out.csv")
        rc = dispatch_command(cli, "extract", args)
        assert rc == 0
        cli.extract_playlist.assert_called_once_with("PL", "out.csv")


# ---- plan ----
class TestDispatchPlan:
    def test_plan_routes_correctly(self, cli):
        cli.plan_playlist = MagicMock()
        args = _make_args(
            playlist="PL", count=8, fresh_days=14,
            generations=3, score_strategy="hybrid", query="chill",
        )
        rc = dispatch_command(cli, "plan", args)
        assert rc == 0
        cli.plan_playlist.assert_called_once_with("PL", 8, 14, 3, "hybrid", "chill")


# ---- diff ----
class TestDispatchDiff:
    def test_diff_routes_correctly(self, cli):
        cli.diff_playlist = MagicMock()
        args = _make_args(
            playlist="PL", count=6, fresh_days=21,
            score_strategy="web", query="jazz",
        )
        rc = dispatch_command(cli, "diff", args)
        assert rc == 0
        cli.diff_playlist.assert_called_once_with("PL", 6, 21, "web", "jazz")


# ---- clean ----
class TestDispatchClean:
    def test_clean_routes_correctly(self, cli):
        cli.clean_database = MagicMock()
        args = _make_args(dry_run=True)
        rc = dispatch_command(cli, "clean", args)
        assert rc == 0
        cli.clean_database.assert_called_once_with(True)


# ---- search ----
class TestDispatchSearch:
    def test_search_routes_correctly(self, cli):
        cli.search_songs = MagicMock()
        args = _make_args(query=["late", "night", "jazz"])
        rc = dispatch_command(cli, "search", args)
        assert rc == 0
        cli.search_songs.assert_called_once_with(["late", "night", "jazz"])


# ---- debug ----
class TestDispatchDebug:
    def test_debug_last_default(self, cli):
        cli.debug_last_search = MagicMock(return_value={"run": {}, "candidates": []})
        args = _make_args(topic="last", value=None, format="json")
        rc = dispatch_command(cli, "debug", args)
        assert rc == 0
        cli.debug_last_search.assert_called_once()

    def test_debug_track_requires_value(self, cli):
        args = _make_args(topic="track", value=None, format="json")
        rc = dispatch_command(cli, "debug", args)
        assert rc == 1

    def test_debug_track_with_value(self, cli):
        cli.debug_track = MagicMock(return_value={"track": {"name": "Song"}})
        args = _make_args(topic="track", value="artist|||song", format="json")
        rc = dispatch_command(cli, "debug", args)
        assert rc == 0
        cli.debug_track.assert_called_once_with("artist|||song")

    def test_debug_no_data(self, cli):
        cli.debug_last_search = MagicMock(return_value=None)
        args = _make_args(topic="last", value=None, format="json")
        rc = dispatch_command(cli, "debug", args)
        assert rc == 1


# ---- ingest ----
class TestDispatchIngest:
    def test_ingest_routes_correctly(self, cli):
        cli.ingest_tracks = MagicMock()
        args = _make_args(source="liked", name=None, time_range="medium_term")
        rc = dispatch_command(cli, "ingest", args)
        assert rc == 0
        cli.ingest_tracks.assert_called_once_with("liked", None, "medium_term")

    def test_ingest_playlist_with_name(self, cli):
        cli.ingest_tracks = MagicMock()
        args = _make_args(source="playlist", name="My PL", time_range="medium_term")
        rc = dispatch_command(cli, "ingest", args)
        assert rc == 0
        cli.ingest_tracks.assert_called_once_with("playlist", "My PL", "medium_term")


# ---- listen-sync ----
class TestDispatchListenSync:
    def test_listen_sync_routes_correctly(self, cli):
        cli.sync_listen_history = MagicMock()
        args = _make_args(limit=50)
        rc = dispatch_command(cli, "listen-sync", args)
        assert rc == 0
        cli.sync_listen_history.assert_called_once_with(50)


# ---- rotate-played ----
class TestDispatchRotatePlayed:
    def test_rotate_played_routes_correctly(self, cli):
        cli.rotate_playlist_played = MagicMock()
        args = _make_args(playlist="PL", max_replace=5)
        rc = dispatch_command(cli, "rotate-played", args)
        assert rc == 0
        cli.rotate_playlist_played.assert_called_once_with("PL", 5)


# ---- rotate ----
class TestDispatchRotate:
    def test_rotate_played_policy(self, cli):
        cli.rotate_playlist_played = MagicMock()
        args = _make_args(playlist="PL", policy="played", max_replace=None)
        rc = dispatch_command(cli, "rotate", args)
        assert rc == 0
        cli.rotate_playlist_played.assert_called_once_with("PL", None)

    def test_rotate_unknown_policy_returns_error(self, cli):
        args = _make_args(playlist="PL", policy="random", max_replace=None)
        rc = dispatch_command(cli, "rotate", args)
        assert rc == 1


# ---- backup ----
class TestDispatchBackup:
    def test_backup_routes_correctly(self, cli):
        cli.backup_data = MagicMock()
        args = _make_args(backup_name="my_backup")
        rc = dispatch_command(cli, "backup", args)
        assert rc == 0
        cli.backup_data.assert_called_once_with("my_backup")


# ---- restore ----
class TestDispatchRestore:
    def test_restore_routes_correctly(self, cli):
        cli.restore_data = MagicMock()
        args = _make_args(backup_name="my_backup")
        rc = dispatch_command(cli, "restore", args)
        assert rc == 0
        cli.restore_data.assert_called_once_with("my_backup")


# ---- restore-previous-rotation ----
class TestDispatchRestorePreviousRotation:
    def test_restore_prev_routes_correctly(self, cli):
        cli.restore_previous_rotation = MagicMock()
        args = _make_args(playlist="PL", offset=-1)
        rc = dispatch_command(cli, "restore-previous-rotation", args)
        assert rc == 0
        cli.restore_previous_rotation.assert_called_once_with("PL", -1)


# ---- list-rotations ----
class TestDispatchListRotations:
    def test_list_rotations_routes_correctly(self, cli):
        cli.list_rotations = MagicMock()
        args = _make_args(playlist="PL", generations="3")
        rc = dispatch_command(cli, "list-rotations", args)
        assert rc == 0
        cli.list_rotations.assert_called_once_with("PL", "3")


# ---- list-backups ----
class TestDispatchListBackups:
    def test_list_backups_routes_correctly(self, cli):
        cli.list_backups = MagicMock()
        args = _make_args()
        rc = dispatch_command(cli, "list-backups", args)
        assert rc == 0
        cli.list_backups.assert_called_once()


# ---- auth-status ----
class TestDispatchAuthStatus:
    def test_auth_status_routes_correctly(self, cli):
        cli.auth_status = MagicMock()
        args = _make_args()
        rc = dispatch_command(cli, "auth-status", args)
        assert rc == 0
        cli.auth_status.assert_called_once()


# ---- auth-refresh ----
class TestDispatchAuthRefresh:
    def test_auth_refresh_routes_correctly(self, cli):
        cli.auth_refresh = MagicMock()
        args = _make_args()
        rc = dispatch_command(cli, "auth-refresh", args)
        assert rc == 0
        cli.auth_refresh.assert_called_once()


# ---- interactive ----
class TestDispatchInteractive:
    def test_interactive_does_not_crash(self, cli):
        args = _make_args()
        rc = dispatch_command(cli, "interactive", args)
        assert rc == 0


# ---- unknown command ----
class TestDispatchUnknown:
    def test_unknown_command_returns_error(self, cli):
        args = _make_args()
        rc = dispatch_command(cli, "nonexistent-command", args)
        assert rc == 1


# ---- exception handling ----
class TestDispatchExceptionHandling:
    def test_exception_in_command_returns_error(self, cli):
        cli.view_playlist = MagicMock(side_effect=RuntimeError("boom"))
        args = _make_args(playlist="PL")
        rc = dispatch_command(cli, "view", args)
        assert rc == 1
