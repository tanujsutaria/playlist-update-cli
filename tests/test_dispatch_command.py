"""
Unit tests for dispatch_command routing.
Verifies that every CLI command routes to the correct PlaylistCLI method.
"""

import argparse
import logging
from unittest.mock import MagicMock

import pytest

import doctor
import main as main_module
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
            playlist="My Playlist",
            count=10,
            fresh_days=30,
            dry_run=False,
            score_strategy="local",
            query=None,
        )
        rc = dispatch_command(cli, "update", args)
        assert rc == 0
        cli.update_playlist.assert_called_once_with(
            "My Playlist",
            10,
            30,
            False,
            "local",
            None,
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
            playlist="PL",
            count=8,
            fresh_days=14,
            generations=3,
            score_strategy="hybrid",
            query="chill",
        )
        rc = dispatch_command(cli, "plan", args)
        assert rc == 0
        cli.plan_playlist.assert_called_once_with("PL", 8, 14, 3, "hybrid", "chill")


# ---- diff ----
class TestDispatchDiff:
    def test_diff_routes_correctly(self, cli):
        cli.diff_playlist = MagicMock()
        args = _make_args(
            playlist="PL",
            count=6,
            fresh_days=21,
            score_strategy="web",
            query="jazz",
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


# ---- search write-through (--to / --save / --replace / --limit) ----
class TestDispatchSearchWriteThrough:
    def _args(self, **over):
        base = dict(query=["jazz"], to_playlist=None, replace=False, save=False, limit=None)
        base.update(over)
        return _make_args(**base)

    def test_to_playlist_appends_by_default(self, cli):
        cli.search_songs = MagicMock()
        cli.add_search_to_playlist = MagicMock()
        cli.mark_search_tracks = MagicMock()
        cli.last_search_track_ids = ["a|||x", "b|||y"]
        rc = dispatch_command(cli, "search", self._args(to_playlist="My Mix"))
        assert rc == 0
        cli.add_search_to_playlist.assert_called_once_with(
            "My Mix", ["a|||x", "b|||y"], replace=False
        )
        cli.mark_search_tracks.assert_not_called()
        assert cli.last_search_handled is True

    def test_replace_and_limit_are_threaded(self, cli):
        cli.search_songs = MagicMock()
        cli.add_search_to_playlist = MagicMock()
        cli.last_search_track_ids = ["a|||x", "b|||y", "c|||z"]
        dispatch_command(cli, "search", self._args(to_playlist="Mix", replace=True, limit=2))
        cli.add_search_to_playlist.assert_called_once_with("Mix", ["a|||x", "b|||y"], replace=True)

    def test_save_marks_accepted_without_writing(self, cli):
        cli.search_songs = MagicMock()
        cli.mark_search_tracks = MagicMock()
        cli.add_search_to_playlist = MagicMock()
        cli.last_search_track_ids = ["a|||x", "b|||y"]
        dispatch_command(cli, "search", self._args(save=True))
        cli.mark_search_tracks.assert_called_once_with(["a|||x", "b|||y"], status="accepted")
        cli.add_search_to_playlist.assert_not_called()
        assert cli.last_search_handled is True

    def test_no_flags_neither_writes_nor_marks(self, cli):
        cli.search_songs = MagicMock()
        cli.mark_search_tracks = MagicMock()
        cli.add_search_to_playlist = MagicMock()
        cli.last_search_track_ids = ["a|||x"]
        cli.last_search_handled = False
        dispatch_command(cli, "search", self._args())
        cli.mark_search_tracks.assert_not_called()
        cli.add_search_to_playlist.assert_not_called()
        assert cli.last_search_handled is False

    def test_no_results_skips_write(self, cli):
        cli.search_songs = MagicMock()
        cli.add_search_to_playlist = MagicMock()
        cli.last_search_track_ids = None
        dispatch_command(cli, "search", self._args(to_playlist="Mix"))
        cli.add_search_to_playlist.assert_not_called()


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


# ---- rotate-played (deprecated alias) ----
class TestDispatchRotatePlayed:
    def test_rotate_played_routes_correctly(self, cli):
        cli.rotate_playlist_played = MagicMock()
        args = _make_args(playlist="PL", max_replace=5)
        rc = dispatch_command(cli, "rotate-played", args)
        assert rc == 0
        cli.rotate_playlist_played.assert_called_once_with("PL", 5)


# ---- rotate ----
class TestDispatchRotate:
    def test_rotate_routes_correctly(self, cli):
        cli.rotate_playlist_played = MagicMock()
        args = _make_args(playlist="PL", max_replace=None, dry_run=False)
        rc = dispatch_command(cli, "rotate", args)
        assert rc == 0
        cli.rotate_playlist_played.assert_called_once_with("PL", None, False)

    def test_rotate_dry_run_routes_correctly(self, cli):
        cli.rotate_playlist_played = MagicMock()
        args = _make_args(playlist="PL", max_replace=None, dry_run=True)
        rc = dispatch_command(cli, "rotate", args)
        assert rc == 0
        cli.rotate_playlist_played.assert_called_once_with("PL", None, True)


# ---- profile ----
class TestDispatchProfile:
    def test_profile_routes_correctly(self, cli):
        cli.show_profile = MagicMock()
        args = _make_args(top=15)
        rc = dispatch_command(cli, "profile", args)
        assert rc == 0
        cli.show_profile.assert_called_once_with(15)


# ---- taste ----
class TestDispatchTaste:
    def test_taste_routes_correctly(self, cli):
        cli.show_taste = MagicMock()
        args = _make_args(top=8)
        rc = dispatch_command(cli, "taste", args)
        assert rc == 0
        cli.show_taste.assert_called_once_with(8)


# ---- undo ----
class TestDispatchUndo:
    def test_undo_routes_correctly(self, cli):
        cli.undo_last_write = MagicMock()
        args = _make_args()
        rc = dispatch_command(cli, "undo", args)
        assert rc == 0
        cli.undo_last_write.assert_called_once()


# ---- enrich ----
class TestDispatchEnrich:
    def test_enrich_routes_correctly(self, cli):
        cli.enrich_library = MagicMock()
        args = _make_args(limit=25, dry_run=False, concurrency=8)
        rc = dispatch_command(cli, "enrich", args)
        assert rc == 0
        cli.enrich_library.assert_called_once_with(
            limit=25, dry_run=False, concurrency=8, cohort=None, playlist=None
        )

    def test_enrich_passes_flags(self, cli):
        cli.enrich_library = MagicMock()
        args = _make_args(limit=100, dry_run=True, concurrency=16)
        dispatch_command(cli, "enrich", args)
        cli.enrich_library.assert_called_once_with(
            limit=100, dry_run=True, concurrency=16, cohort=None, playlist=None
        )

    def test_enrich_routes_cohort_flag(self, cli):
        cli.enrich_library = MagicMock()
        args = _make_args(limit=25, dry_run=False, concurrency=8, liked=True)
        dispatch_command(cli, "enrich", args)
        cli.enrich_library.assert_called_once_with(
            limit=25, dry_run=False, concurrency=8, cohort="liked", playlist=None
        )


# ---- sonic ----
class TestDispatchSonic:
    def test_sonic_routes_correctly(self, cli):
        cli.sonic_backfill = MagicMock()
        args = _make_args(limit=50, dry_run=False)
        rc = dispatch_command(cli, "sonic", args)
        assert rc == 0
        cli.sonic_backfill.assert_called_once_with(
            limit=50, dry_run=False, cohort=None, playlist=None
        )

    def test_sonic_routes_playlist_cohort(self, cli):
        cli.sonic_backfill = MagicMock()
        args = _make_args(limit=50, dry_run=False, playlist="Daily Mix")
        dispatch_command(cli, "sonic", args)
        cli.sonic_backfill.assert_called_once_with(
            limit=50, dry_run=False, cohort="playlist", playlist="Daily Mix"
        )


# ---- embed ----
class TestDispatchEmbed:
    def test_embed_routes_correctly(self, cli):
        cli.embed_backfill = MagicMock()
        args = _make_args(limit=None, dry_run=False)
        rc = dispatch_command(cli, "embed", args)
        assert rc == 0
        cli.embed_backfill.assert_called_once_with(limit=None, dry_run=False)

    def test_embed_passes_flags(self, cli):
        cli.embed_backfill = MagicMock()
        args = _make_args(limit=500, dry_run=True)
        dispatch_command(cli, "embed", args)
        cli.embed_backfill.assert_called_once_with(limit=500, dry_run=True)


# ---- similar ----
class TestDispatchSimilar:
    def _payload(self):
        return {
            "query": "a|||one",
            "seed": {"track_id": "a|||one", "label": "one — A"},
            "results": [
                {
                    "track_id": "b|||two",
                    "song": "two",
                    "artist": "B",
                    "similarity": 0.91,
                    "basis": "title",
                    "spotify_url": "",
                }
            ],
        }

    def test_similar_routes_correctly(self, cli):
        cli.similar_tracks = MagicMock(return_value=self._payload())
        args = _make_args(query=["a|||one"], limit=10, to_playlist=None, json=False)
        rc = dispatch_command(cli, "similar", args)
        assert rc == 0
        cli.similar_tracks.assert_called_once_with("a|||one", limit=10)

    def test_similar_joins_free_text_query(self, cli):
        cli.similar_tracks = MagicMock(return_value=self._payload())
        args = _make_args(query=["late", "night", "jazz"], limit=5, to_playlist=None, json=False)
        rc = dispatch_command(cli, "similar", args)
        assert rc == 0
        cli.similar_tracks.assert_called_once_with("late night jazz", limit=5)

    def test_similar_to_writes_through_add_search_to_playlist(self, cli):
        cli.similar_tracks = MagicMock(return_value=self._payload())
        cli.add_search_to_playlist = MagicMock(return_value=True)
        args = _make_args(query=["a|||one"], limit=10, to_playlist="My Mix", json=False)
        rc = dispatch_command(cli, "similar", args)
        assert rc == 0
        cli.add_search_to_playlist.assert_called_once_with("My Mix", ["b|||two"])

    def test_similar_no_results_returns_error(self, cli):
        cli.similar_tracks = MagicMock(return_value={"query": "x", "seed": None, "results": []})
        cli.add_search_to_playlist = MagicMock()
        args = _make_args(query=["x"], limit=10, to_playlist="My Mix", json=False)
        rc = dispatch_command(cli, "similar", args)
        assert rc == 1
        cli.add_search_to_playlist.assert_not_called()


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


# ---- auth-reset ----
class TestDispatchAuthReset:
    def test_auth_reset_routes_yes_flag(self, cli):
        cli.auth_reset = MagicMock()
        args = _make_args(yes=True)
        rc = dispatch_command(cli, "auth-reset", args)
        assert rc == 0
        cli.auth_reset.assert_called_once_with(yes=True)

    def test_auth_reset_defaults_to_unconfirmed(self, cli):
        cli.auth_reset = MagicMock()
        args = _make_args()
        rc = dispatch_command(cli, "auth-reset", args)
        assert rc == 0
        cli.auth_reset.assert_called_once_with(yes=False)


# ---- interactive ----
class TestDispatchInteractive:
    def test_interactive_does_not_crash(self, cli):
        args = _make_args()
        rc = dispatch_command(cli, "interactive", args)
        assert rc == 0


# ---- doctor ----
class TestDispatchDoctor:
    def test_doctor_runs_offline_and_returns_zero(self, cli, tmp_path, monkeypatch):
        """/doctor audits the (isolated, fresh) DB entirely offline: the lazy
        storage property opens the TUNR_DB_PATH tmp database, no Spotify is
        touched, and a healthy DB exits 0. The backups dir is sandboxed so the
        check never reads the repo's real backups/ state dir."""
        monkeypatch.setattr(doctor, "default_backups_dir", lambda: tmp_path / "backups")
        args = _make_args(json=False)
        rc = dispatch_command(cli, "doctor", args)
        assert rc == 0


# ---- add / remove / move (quick track ops) ----
class TestDispatchAdd:
    def test_add_routes_correctly(self, cli):
        cli.add_track_to_playlist = MagicMock(return_value=True)
        args = _make_args(query=["wild", "nothing"], to_playlist="Mix", track_id=None)
        rc = dispatch_command(cli, "add", args)
        assert rc == 0
        cli.add_track_to_playlist.assert_called_once_with("wild nothing", "Mix", track_id=None)

    def test_add_threads_id_bypass(self, cli):
        cli.add_track_to_playlist = MagicMock(return_value=True)
        args = _make_args(query=[], to_playlist="Mix", track_id="a|||b")
        dispatch_command(cli, "add", args)
        cli.add_track_to_playlist.assert_called_once_with("", "Mix", track_id="a|||b")

    def test_add_failure_returns_error(self, cli):
        cli.add_track_to_playlist = MagicMock(return_value=False)
        args = _make_args(query=["x"], to_playlist="Mix", track_id=None)
        assert dispatch_command(cli, "add", args) == 1


class TestDispatchRemove:
    def test_remove_routes_correctly(self, cli):
        cli.remove_track_from_playlist = MagicMock(return_value=True)
        args = _make_args(query=["shadow"], from_playlist="Mix", track_id=None)
        rc = dispatch_command(cli, "remove", args)
        assert rc == 0
        cli.remove_track_from_playlist.assert_called_once_with("shadow", "Mix", track_id=None)

    def test_remove_failure_returns_error(self, cli):
        cli.remove_track_from_playlist = MagicMock(return_value=False)
        args = _make_args(query=["shadow"], from_playlist="Mix", track_id=None)
        assert dispatch_command(cli, "remove", args) == 1


class TestDispatchMove:
    def test_move_routes_correctly(self, cli):
        cli.move_track = MagicMock(return_value=True)
        args = _make_args(query=["shadow"], from_playlist="Mix", to_playlist="Chill", track_id=None)
        rc = dispatch_command(cli, "move", args)
        assert rc == 0
        cli.move_track.assert_called_once_with("shadow", "Mix", "Chill", track_id=None)

    def test_move_failure_returns_error(self, cli):
        cli.move_track = MagicMock(return_value=False)
        args = _make_args(query=["shadow"], from_playlist="Mix", to_playlist="Chill", track_id=None)
        assert dispatch_command(cli, "move", args) == 1


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

    def test_exception_logs_traceback_with_exc_info(self, cli, caplog, monkeypatch):
        """A crashed handler must log the REAL traceback (exc_info), not just the
        one-line repr — UILogHandler's Formatter appends exc_text, so this is
        what gets the stack into the TUI RichLog and the /debug errors ring."""

        def _boom(cli_arg, args):
            raise KeyError("kaboom")

        monkeypatch.setitem(main_module._COMMAND_HANDLERS, "boom-test", _boom)
        with caplog.at_level(logging.ERROR, logger="main"):
            rc = dispatch_command(cli, "boom-test", _make_args())

        assert rc == 1  # the rc contract is unchanged
        failures = [r for r in caplog.records if "Command failed" in r.getMessage()]
        assert failures, "expected a 'Command failed' record"
        record = failures[-1]
        assert record.exc_info is not None
        assert record.exc_info[0] is KeyError
        # A plain Formatter (what the TUI handler uses) renders the stack.
        rendered = logging.Formatter("%(message)s").format(record)
        assert "Traceback" in rendered
        assert "kaboom" in rendered


class TestNoisyLoggerSilencing:
    """configure_logging must keep huggingface/httpx INFO spam out of the UI:
    the embedding-model cache check logs ~10 "HTTP Request:" lines per load."""

    NOISY = ("httpx", "httpcore", "huggingface_hub", "urllib3")

    def test_noisy_loggers_raised_to_warning(self):
        import logging

        from main import configure_logging

        # configure_logging mutates process-global logging state (root level,
        # root handlers, named-logger levels); snapshot and restore so later
        # tests that depend on the default root level aren't poisoned.
        root = logging.getLogger()
        saved_root_level, saved_root_handlers = root.level, root.handlers[:]
        saved_levels = {name: logging.getLogger(name).level for name in self.NOISY}
        try:
            configure_logging(handler=logging.NullHandler())
            for name in self.NOISY:
                assert logging.getLogger(name).level == logging.WARNING
        finally:
            for name, level in saved_levels.items():
                logging.getLogger(name).setLevel(level)
            root.handlers = saved_root_handlers
            root.setLevel(saved_root_level)


# ---- linked track tables ----
class TestDebugTablesCarrySpotifyLinks:
    """Track tables that carry Spotify ids render the track name as an OSC 8
    hyperlink — visible text unchanged, rows without an id stay plain."""

    def test_top_results_track_cell_links_known_spotify_id(self):
        from rich.table import Table

        import ui

        payload = {
            "run": {"run_id": "r1"},
            "candidates": [
                {
                    "track_id": "a|||one",
                    "track": {"name": "One", "artist_name": "A", "spotify_id": "spotify:track:abc"},
                },
                {
                    "track_id": "b|||two",
                    "track": {"name": "Two", "artist_name": "B", "spotify_id": None},
                },
            ],
            "summary": {},
        }
        captured = []
        ui.set_output_sink(captured.append)
        try:
            main_module._present_debug_last_search(payload)
        finally:
            ui.set_output_sink(None)
        top_results = [r for r in captured if isinstance(r, Table)][-1]
        linked_cell, plain_cell = list(top_results.columns[1].cells)
        assert linked_cell.plain == "One — A"  # visible text is just the label
        assert [
            span.style.link for span in linked_cell.spans if getattr(span.style, "link", None)
        ] == ["https://open.spotify.com/track/abc"]
        # No Spotify identity -> no link span anywhere on the cell.
        assert plain_cell.plain == "Two — B"
        assert not [span for span in plain_cell.spans if getattr(span.style, "link", None)]
