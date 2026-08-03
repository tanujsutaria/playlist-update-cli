"""Unit tests for the fuzzy playlist-name resolver (suggest-on-miss).

The resolver is SUGGEST-ONLY: on a miss the handlers render "did you mean"
candidates but never substitute a guessed name or execute against it. Exact
(and case-insensitive) matches never reach the resolver at all.
"""

from __future__ import annotations

import sqlite3
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rich.console import Console

import ui
from main import PlaylistCLI
from playlist_resolver import (
    collect_playlist_names,
    playlist_not_found_message,
    report_playlist_miss,
    suggest_playlist_names,
    warn_if_unknown_playlist,
)
from storage.migrations import ensure_schema
from storage.repos import Repositories


@pytest.fixture
def sink():
    """Capture everything the ui helpers emit (panels, warnings, …)."""
    captured = []
    ui.set_output_sink(captured.append)
    yield captured
    ui.set_output_sink(None)


def _rendered(captured, width: int = 120) -> str:
    buf = StringIO()
    console = Console(file=buf, width=width)
    for renderable in captured:
        console.print(renderable)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pure suggestion ranking
# ---------------------------------------------------------------------------


class TestSuggestPlaylistNames:
    CANDIDATES = ["My Daily Mix", "Daily Drive", "Road Trip Bangers"]

    def test_close_match_returns_ranked_candidates(self):
        suggestions = suggest_playlist_names("dailymix", self.CANDIDATES)
        assert "My Daily Mix" in suggestions
        assert "Daily Drive" in suggestions
        assert "Road Trip Bangers" not in suggestions

    def test_no_match_returns_empty(self):
        assert suggest_playlist_names("zzzzzz", self.CANDIDATES) == []

    def test_empty_candidates_return_empty(self):
        assert suggest_playlist_names("anything", []) == []

    def test_case_duplicates_collapse_to_first_spelling(self):
        suggestions = suggest_playlist_names("chil", ["Chill", "chill", "CHILL"])
        assert suggestions == ["Chill"]

    def test_limit_respected(self):
        candidates = ["mix 1", "mix 2", "mix 3", "mix 4"]
        assert len(suggest_playlist_names("mix", candidates, limit=2)) == 2


class TestPlaylistNotFoundMessage:
    def test_with_suggestions_names_them_and_stays_suggest_only(self):
        message = playlist_not_found_message("dailymix", ["My Daily Mix", "Daily Drive"])
        assert "no playlist 'dailymix'" in message
        assert "did you mean: My Daily Mix, Daily Drive?" in message
        # Suggest-only contract is stated to the user, not just implied.
        assert "exact name" in message

    def test_without_suggestions_points_at_pull(self):
        message = playlist_not_found_message("dailymix", [])
        assert "no playlist 'dailymix'" in message
        assert "/pull" in message


# ---------------------------------------------------------------------------
# Candidate collection (live cache + rotation table + mirror; all best-effort)
# ---------------------------------------------------------------------------


def _repos_with_names() -> Repositories:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    repos = Repositories(conn)
    repos.playlists.upsert(playlist_id="p1", name="Rotation One")
    repos.spotify_playlists.upsert(spotify_playlist_id="s1", name="Mirrored One")
    return repos


class TestCollectPlaylistNames:
    def test_live_cache_only_when_repos_not_materialized(self):
        cli = PlaylistCLI.__new__(PlaylistCLI)
        cli._spotify = SimpleNamespace(playlists={"A": "1", "B": "2"})
        # No _repos attribute at all: the lazy property must NOT be triggered
        # (that would create a database as a side effect of a suggestion).
        assert set(collect_playlist_names(cli)) == {"A", "B"}
        assert getattr(cli, "_repos", None) is None

    def test_rotation_table_and_mirror_names_included(self):
        cli = PlaylistCLI.__new__(PlaylistCLI)
        cli._spotify = None
        cli._repos = _repos_with_names()
        assert set(collect_playlist_names(cli)) == {"Rotation One", "Mirrored One"}

    def test_sources_merge_and_dedupe_case_insensitively(self):
        cli = PlaylistCLI.__new__(PlaylistCLI)
        cli._spotify = SimpleNamespace(playlists={"rotation one": "x", "Live Only": "y"})
        cli._repos = _repos_with_names()
        names = collect_playlist_names(cli)
        lowered = [name.lower() for name in names]
        assert lowered.count("rotation one") == 1  # deduped across sources
        assert "live only" in lowered
        assert "mirrored one" in lowered

    def test_non_dict_playlists_attribute_tolerated(self):
        cli = PlaylistCLI.__new__(PlaylistCLI)
        cli._spotify = MagicMock()  # .playlists is a MagicMock, not a dict
        cli._repos = None
        assert collect_playlist_names(cli) == []


# ---------------------------------------------------------------------------
# Handler wiring: misses render suggestions, exact matches stay untouched
# ---------------------------------------------------------------------------


def _spotify_with(names: dict) -> MagicMock:
    spotify = MagicMock()
    spotify.playlists = dict(names)
    spotify.get_playlist_id.side_effect = lambda name: spotify.playlists.get(name)
    return spotify


def _cli() -> PlaylistCLI:
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._spotify = _spotify_with({"My Daily Mix": "1", "Daily Drive": "2"})
    cli._db = MagicMock()
    cli._rotation_managers = {}
    return cli


class TestMissRenderingInHandlers:
    def test_view_miss_renders_suggestions_and_reads_nothing(self, sink):
        cli = _cli()
        cli.view_playlist("dailymix")
        cli._spotify.get_playlist_tracks.assert_not_called()
        text = _rendered(sink)
        assert "no playlist 'dailymix'" in text
        assert "My Daily Mix" in text
        assert "Daily Drive" in text

    def test_view_exact_match_untouched(self, sink):
        cli = _cli()
        cli._spotify.get_playlist_tracks.return_value = []
        cli.view_playlist("My Daily Mix")
        cli._spotify.get_playlist_tracks.assert_called_once_with("My Daily Mix")
        assert "no playlist" not in _rendered(sink)

    def test_sync_miss_blocks_before_touching_the_database(self, sink):
        cli = _cli()
        cli.sync_playlist("dailymix")
        cli._db.get_all_songs.assert_not_called()
        cli._spotify.append_to_playlist.assert_not_called()
        assert "did you mean" in _rendered(sink)

    def test_extract_miss_returns_false_and_writes_no_file(self, sink, tmp_path):
        cli = _cli()
        target = tmp_path / "out.csv"
        assert cli.extract_playlist("dailymix", str(target)) is False
        assert not target.exists()
        assert "did you mean" in _rendered(sink)

    def test_rotate_miss_renders_suggestions(self, sink):
        cli = _cli()
        cli.rotate_playlist_played("dailymix")
        cli._spotify.get_playlist_tracks.assert_not_called()
        assert "did you mean" in _rendered(sink)

    def test_diff_miss_renders_suggestions(self, sink):
        cli = _cli()
        cli.diff_playlist("dailymix", 10, 30)
        cli._spotify.get_playlist_tracks.assert_not_called()
        assert "did you mean" in _rendered(sink)

    def test_report_playlist_miss_is_suggest_only(self, sink):
        # The central renderer itself: it prints, it never mutates or runs.
        cli = _cli()
        report_playlist_miss(cli, "dailymix")
        text = _rendered(sink)
        assert "did you mean" in text
        cli._spotify.get_playlist_tracks.assert_not_called()
        cli._spotify.refresh_playlist.assert_not_called()


class TestWarnIfUnknownPlaylist:
    def test_known_name_stays_quiet(self, sink):
        cli = _cli()
        warn_if_unknown_playlist(cli, "My Daily Mix")
        assert sink == []

    def test_known_name_case_insensitive_stays_quiet(self, sink):
        cli = _cli()
        warn_if_unknown_playlist(cli, "my daily mix")
        assert sink == []

    def test_near_miss_warns_but_never_blocks(self, sink):
        cli = _cli()
        warn_if_unknown_playlist(cli, "dailymix")
        text = _rendered(sink)
        assert "Did you mean" in text
        assert "My Daily Mix" in text

    def test_brand_new_name_stays_quiet(self, sink):
        cli = _cli()
        warn_if_unknown_playlist(cli, "Completely Fresh Concept")
        assert sink == []
