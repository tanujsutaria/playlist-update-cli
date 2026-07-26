"""Tests for the /results browser (src/results_screen.py + /results routing).

Covers: the pure row-building functions (empty results, missing metrics, long
fields, /find's blended-score fallback), the browse-order preference
(last_find_ranked over last_search_results), the offline spotify-url helpers,
and Pilot-driven screen behavior (cursor + i inspect, space selection, the
row actions — enter prefill, c copy id, o print link (never a browser) —
the in-screen playlist prompt, and the dismissal payloads). All offline —
the cli is a stub, no network, no real Spotify.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from main import PlaylistCLI
from results_screen import (
    MAX_FIELD_CHARS,
    ResultsAction,
    ResultsScreen,
    prefill_for_item,
    results_for_browse,
    rows_for_table,
    spotify_url_for_item,
    track_id_of,
)

RESULTS = [
    {
        "song": "Aligned",
        "artist": "Result Artist",
        "year": "2012",
        "score": 0.25,
        "providers": ["anthropic"],
        "sources": ["https://example.com/a"],
        "track_id": "r|||aligned",
        "metrics": {"bpm": 120.0},
    },
    {
        "song": "Offbeat",
        "artist": "Result Artist",
        "year": None,
        "score": 0.9,
        "providers": [],
        "sources": [],
        "track_id": "r|||offbeat",
        "metrics": {},
    },
    {
        "song": "Third",
        "artist": "Other Artist",
        "year": "1999",
        "score": 0.5,
        "providers": [],
        "sources": [],
        "track_id": "o|||third",
    },
]


def _stub_cli(results=None, ranked=None, debug_payload=None, url=""):
    return SimpleNamespace(
        last_search_results=results,
        last_find_ranked=ranked,
        last_search_query="dreamy shoegaze",
        debug_track=lambda track_id: debug_payload,
        spotify_url_for_track=lambda track_id: url,
    )


# ---------------------------------------------------------------------------
# rows_for_table (pure)
# ---------------------------------------------------------------------------


class TestRowsForTable:
    def test_empty_results(self):
        assert rows_for_table([]) == ([], [])

    def test_headers_include_metric_union_in_first_seen_order(self):
        results = [
            {"song": "A", "artist": "X", "metrics": {"bpm": 100}},
            {"song": "B", "artist": "Y", "metrics": {"energy": 0.5, "bpm": 90}},
        ]
        headers, rows = rows_for_table(results)
        assert headers == ["#", "song", "artist", "year", "score", "bpm", "energy"]
        assert len(rows) == 2 and all(len(row) == len(headers) for row in rows)

    def test_missing_metrics_render_placeholders(self):
        headers, rows = rows_for_table(RESULTS)
        assert "bpm" in headers
        bpm_col = headers.index("bpm")
        assert rows[0][bpm_col] == "120"
        assert rows[1][bpm_col] == "—"  # empty metrics dict
        assert rows[2][bpm_col] == "—"  # no metrics key at all

    def test_long_fields_are_clipped(self):
        long_name = "x" * 200
        headers, rows = rows_for_table([{"song": long_name, "artist": "a"}])
        assert len(rows[0][1]) == MAX_FIELD_CHARS
        assert rows[0][1].endswith("…")

    def test_missing_year_and_score(self):
        headers, rows = rows_for_table([{"song": "A", "artist": "B"}])
        year_col, score_col = headers.index("year"), headers.index("score")
        assert rows[0][year_col] == "—"
        assert rows[0][score_col] == "—"

    def test_find_rows_use_blended_score(self):
        headers, rows = rows_for_table([{"song": "A", "artist": "B", "blended": 0.75}])
        assert rows[0][headers.index("score")] == "0.750"

    def test_rank_column_is_one_based(self):
        _, rows = rows_for_table(RESULTS)
        assert [row[0] for row in rows] == ["1", "2", "3"]


class TestTrackIdOf:
    def test_explicit_id_wins(self):
        assert track_id_of({"track_id": "a|||b", "song": "X", "artist": "Y"}) == "a|||b"

    def test_rebuilt_from_song_and_artist(self):
        assert track_id_of({"song": "Song", "artist": "Artist"}) == "artist|||song"

    def test_empty_when_unresolvable(self):
        assert track_id_of({"song": "Song"}) == ""


# ---------------------------------------------------------------------------
# results_for_browse — the /find-vs-/search ordering decision
# ---------------------------------------------------------------------------


class TestResultsForBrowse:
    def test_prefers_find_ranked_order(self):
        ranked = [dict(RESULTS[1]), dict(RESULTS[0])]  # taste order != search order
        cli = _stub_cli(results=list(RESULTS), ranked=ranked)
        rows = results_for_browse(cli)
        assert [r["track_id"] for r in rows] == ["r|||offbeat", "r|||aligned"]

    def test_falls_back_to_search_results(self):
        cli = _stub_cli(results=list(RESULTS), ranked=None)
        rows = results_for_browse(cli)
        assert [r["track_id"] for r in rows] == ["r|||aligned", "r|||offbeat", "o|||third"]

    def test_empty_when_nothing_cached(self):
        assert results_for_browse(_stub_cli()) == []

    def test_copies_rows(self):
        cli = _stub_cli(results=[dict(RESULTS[0])])
        rows = results_for_browse(cli)
        rows[0]["song"] = "mutated"
        assert cli.last_search_results[0]["song"] == "Aligned"


# ---------------------------------------------------------------------------
# spotify url helpers (offline)
# ---------------------------------------------------------------------------


class TestSpotifyUrlForItem:
    def test_url_passthrough(self):
        assert spotify_url_for_item({"spotify_url": "https://x"}) == "https://x"

    def test_uri_converted(self):
        item = {"spotify_uri": "spotify:track:abc123"}
        assert spotify_url_for_item(item) == "https://open.spotify.com/track/abc123"

    def test_http_uri_passthrough(self):
        assert spotify_url_for_item({"spotify_uri": "https://y"}) == "https://y"

    def test_no_link(self):
        assert spotify_url_for_item({"song": "A"}) == ""


class TestSpotifyUrlForTrack:
    """PlaylistCLI.spotify_url_for_track against a stubbed repos slice."""

    def _cli(self, record):
        cli = PlaylistCLI.__new__(PlaylistCLI)
        cli._repos = SimpleNamespace(tracks=SimpleNamespace(get=lambda track_id: record))
        return cli

    def test_uri_record(self):
        cli = self._cli({"spotify_id": "spotify:track:abc"})
        assert cli.spotify_url_for_track("a|||b") == "https://open.spotify.com/track/abc"

    def test_bare_id_record(self):
        cli = self._cli({"spotify_id": "3n3Ppam7vgaVa1iaRUc9Lp"})
        assert (
            cli.spotify_url_for_track("a|||b")
            == "https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp"
        )

    def test_http_record(self):
        cli = self._cli({"spotify_id": "https://open.spotify.com/track/z"})
        assert cli.spotify_url_for_track("a|||b") == "https://open.spotify.com/track/z"

    def test_unrecognized_uri_is_not_a_link(self):
        cli = self._cli({"spotify_id": "spotify:album:zzz"})
        assert cli.spotify_url_for_track("a|||b") == ""

    def test_missing_record_or_id(self):
        assert self._cli(None).spotify_url_for_track("a|||b") == ""
        assert self._cli({"spotify_id": None}).spotify_url_for_track("a|||b") == ""
        assert self._cli({"spotify_id": "x"}).spotify_url_for_track("") == ""


# ---------------------------------------------------------------------------
# prefill_for_item — the enter row action's editable /find command (pure)
# ---------------------------------------------------------------------------


class TestPrefillForItem:
    def test_song_and_artist(self):
        assert (
            prefill_for_item({"song": "Aligned", "artist": "Result Artist"})
            == '/find "more like Aligned by Result Artist"'
        )

    def test_apostrophes_survive_shlex(self):
        import shlex

        command = prefill_for_item({"song": "Don't Stop", "artist": "Fleetwood Mac"})
        tokens = shlex.split(command.lstrip("/"))
        assert tokens == ["find", "more like Don't Stop by Fleetwood Mac"]

    def test_double_quotes_escaped(self):
        import shlex

        command = prefill_for_item({"song": 'Say "Yes"', "artist": "A"})
        tokens = shlex.split(command.lstrip("/"))
        assert tokens == ["find", 'more like Say "Yes" by A']

    def test_falls_back_to_track_id(self):
        assert (
            prefill_for_item({"track_id": "some artist|||some song"})
            == '/find "more like some song by some artist"'
        )

    def test_partial_row_uses_what_exists(self):
        assert prefill_for_item({"song": "Solo"}) == '/find "more like Solo"'

    def test_empty_when_unresolvable(self):
        assert prefill_for_item({}) == ""


# ---------------------------------------------------------------------------
# /find wiring: a fresh CLI starts with no ranked order; resets clear it
# ---------------------------------------------------------------------------


class TestLastFindRankedState:
    def test_fresh_cli_has_no_find_ranking(self):
        assert PlaylistCLI().last_find_ranked is None

    def test_reset_clears_find_ranking(self):
        cli = PlaylistCLI.__new__(PlaylistCLI)
        cli.last_find_ranked = [{"track_id": "x"}]
        cli._reset_search_state()
        assert cli.last_find_ranked is None


# ---------------------------------------------------------------------------
# Pilot: push the screen, drive the keys, inspect the dismissal payloads
# ---------------------------------------------------------------------------


def _drive(coro):
    """asyncio.run + the py3.9 event-loop restore (see test_dashboard.py)."""
    import asyncio

    try:
        asyncio.run(coro)
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _smoke_app():
    from textual.app import App

    class SmokeApp(App):
        pass

    return SmokeApp()


def _host_app():
    """A bare host app exposing the two seams the row actions duck-type:
    append_log (scrollback confirmations) and copy_to_clipboard."""
    from textual.app import App

    class HostApp(App):
        def __init__(self):
            super().__init__()
            self.logged = []
            self.copied = []

        def append_log(self, renderable):
            self.logged.append(renderable)

        def copy_to_clipboard(self, text):
            self.copied.append(text)

    return HostApp()


DEBUG_PAYLOAD = {
    "track": {"track_id": "r|||aligned", "status": "candidate", "spotify_id": "spotify:track:a"},
    "context": {"strict_ratio": 0.42},
    "sources": [{"url": "https://example.com"}],
    "embedding": {},
    "listens": [],
}


class TestResultsScreenPilot:
    def test_cursor_inspect_select_accept(self):
        from textual.widgets import DataTable, Static

        cli = _stub_cli(results=[dict(r) for r in RESULTS], debug_payload=DEBUG_PAYLOAD)
        dismissed = []

        async def drive():
            app = _smoke_app()
            async with app.run_test(size=(100, 30)) as pilot:
                screen = ResultsScreen(cli)
                await app.push_screen(screen, callback=dismissed.append)
                await pilot.pause()
                table = screen.query_one(DataTable)
                assert table.row_count == 3
                assert table.cursor_row == 0
                # Cursor moves; i inspects via cli.debug_track's payload
                # (enter is the prefill row action now).
                await pilot.press("down")
                assert table.cursor_row == 1
                await pilot.press("i")
                detail = screen.query_one("#results_detail", Static)
                readout = detail.render() if callable(detail.render) else ""
                text = str(readout)
                assert "sources" in text and "strict 0.42" in text
                # Space toggles per-row selection (marker + footer count).
                await pilot.press("space")
                assert screen.selected == {1}
                footer = str(screen.query_one("#results_footer", Static).render())
                assert "1 selected" in footer
                await pilot.press("space")
                assert screen.selected == set()
                # Re-select rows 2 and 1 and accept the subset.
                await pilot.press("space")
                await pilot.press("down", "space")
                assert screen.selected == {1, 2}
                await pilot.press("a")
                await pilot.pause()
                assert app.screen is not screen

        _drive(drive())
        assert len(dismissed) == 1
        action = dismissed[0]
        assert isinstance(action, ResultsAction)
        assert action.mode == "db"
        assert action.track_ids == ["r|||offbeat", "o|||third"]
        assert action.playlist_name is None

    def test_accept_without_selection_uses_cursor_row(self):
        cli = _stub_cli(results=[dict(r) for r in RESULTS])
        dismissed = []

        async def drive():
            app = _smoke_app()
            async with app.run_test(size=(100, 30)) as pilot:
                screen = ResultsScreen(cli)
                await app.push_screen(screen, callback=dismissed.append)
                await pilot.pause()
                await pilot.press("down", "a")
                await pilot.pause()

        _drive(drive())
        assert dismissed[0].track_ids == ["r|||offbeat"]

    def test_playlist_prompt_stays_in_screen(self):
        from textual.widgets import Input

        cli = _stub_cli(results=[dict(r) for r in RESULTS])
        dismissed = []

        async def drive():
            app = _smoke_app()
            async with app.run_test(size=(100, 30)) as pilot:
                screen = ResultsScreen(cli)
                await app.push_screen(screen, callback=dismissed.append)
                await pilot.pause()
                await pilot.press("space", "p")
                prompt = screen.query_one("#results_prompt", Input)
                assert prompt.display and prompt.has_focus
                # Esc cancels the prompt, NOT the screen.
                await pilot.press("escape")
                await pilot.pause()
                assert not prompt.display
                assert app.screen is screen
                # Re-open, type a name, submit -> dismiss with the payload.
                await pilot.press("p")
                await pilot.press(*"mix")
                await pilot.press("enter")
                await pilot.pause()
                assert app.screen is not screen

        _drive(drive())
        assert len(dismissed) == 1
        action = dismissed[0]
        assert action.mode == "playlist"
        assert action.playlist_name == "mix"
        assert action.track_ids == ["r|||aligned"]

    def test_spotify_url_prints_and_never_opens_browser(self, monkeypatch):
        import webbrowser

        from textual.widgets import Static

        def _no_browser(*args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("the o action must never open a browser")

        monkeypatch.setattr(webbrowser, "open", _no_browser)

        # First: db fallback provides the url (rows carry none); it is
        # PRINTED — detail readout + app scrollback — never opened.
        cli = _stub_cli(
            results=[dict(r) for r in RESULTS],
            url="https://open.spotify.com/track/abc",
        )

        async def drive_with_url():
            app = _host_app()
            async with app.run_test(size=(100, 30)) as pilot:
                screen = ResultsScreen(cli)
                await app.push_screen(screen)
                await pilot.pause()
                await pilot.press("o")
                detail = str(screen.query_one("#results_detail", Static).render())
                assert "https://open.spotify.com/track/abc" in detail
                logged = "\n".join(str(line) for line in app.logged)
                assert "Aligned — Result Artist: https://open.spotify.com/track/abc" in logged
                await pilot.press("escape")
                await pilot.pause()

        _drive(drive_with_url())

        # Second: no cached url anywhere -> graceful status, nothing logged.
        cli_no_url = _stub_cli(results=[dict(r) for r in RESULTS], url="")

        async def drive_without_url():
            app = _host_app()
            async with app.run_test(size=(100, 30)) as pilot:
                screen = ResultsScreen(cli_no_url)
                await app.push_screen(screen)
                await pilot.pause()
                await pilot.press("o")
                detail = str(screen.query_one("#results_detail", Static).render())
                assert "no spotify link" in detail
                assert app.logged == []
                await pilot.press("escape")
                await pilot.pause()

        _drive(drive_without_url())

    def test_escape_dismisses_with_none(self):
        cli = _stub_cli(results=[dict(r) for r in RESULTS])
        dismissed = []

        async def drive():
            app = _smoke_app()
            async with app.run_test(size=(100, 30)) as pilot:
                screen = ResultsScreen(cli)
                await app.push_screen(screen, callback=dismissed.append)
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                assert app.screen is not screen

        _drive(drive())
        assert dismissed == [None]

    def test_row_priority_uses_find_ranking(self):
        from textual.widgets import DataTable

        ranked = [dict(RESULTS[2]), dict(RESULTS[0])]
        cli = _stub_cli(results=[dict(r) for r in RESULTS], ranked=ranked)

        async def drive():
            app = _smoke_app()
            async with app.run_test(size=(100, 30)) as pilot:
                screen = ResultsScreen(cli)
                await app.push_screen(screen)
                await pilot.pause()
                table = screen.query_one(DataTable)
                assert table.row_count == 2  # the /find set, not the raw search set
                assert screen.results[0]["track_id"] == "o|||third"
                await pilot.press("escape")
                await pilot.pause()

        _drive(drive())


# ---------------------------------------------------------------------------
# Row actions: enter prefill, c copy id (screen-level and end-to-end)
# ---------------------------------------------------------------------------


class TestRowActionsPilot:
    def test_enter_dismisses_with_prefill_action(self):
        cli = _stub_cli(results=[dict(r) for r in RESULTS])
        dismissed = []

        async def drive():
            app = _smoke_app()
            async with app.run_test(size=(100, 30)) as pilot:
                screen = ResultsScreen(cli)
                await app.push_screen(screen, callback=dismissed.append)
                await pilot.pause()
                await pilot.press("down")  # row-aware: acts on the cursor row
                await pilot.press("enter")
                await pilot.pause()
                assert app.screen is not screen

        _drive(drive())
        assert len(dismissed) == 1
        action = dismissed[0]
        assert isinstance(action, ResultsAction)
        assert action.mode == "prefill"
        assert action.prefill == '/find "more like Offbeat by Result Artist"'
        assert action.track_ids == []

    def test_copy_key_copies_id_and_logs_confirmation(self):
        from textual.widgets import Static

        cli = _stub_cli(results=[dict(r) for r in RESULTS])

        async def drive():
            app = _host_app()
            async with app.run_test(size=(100, 30)) as pilot:
                screen = ResultsScreen(cli)
                await app.push_screen(screen)
                await pilot.pause()
                await pilot.press("c")
                assert app.copied == ["r|||aligned"]
                detail = str(screen.query_one("#results_detail", Static).render())
                assert "copied r|||aligned" in detail
                logged = "\n".join(str(line) for line in app.logged)
                assert "Copied track id to clipboard: r|||aligned" in logged
                # The screen stays up: copying is not a dismissal.
                assert app.screen is screen
                await pilot.press("escape")
                await pilot.pause()

        _drive(drive())

    def test_enter_prefills_the_main_command_input(self, monkeypatch):
        """End-to-end: /results in the real app; enter closes the browser and
        preloads an editable /find into the command input — never submits."""
        import logging as _logging

        from textual.widgets import Input

        import ui as _ui
        from arg_parse import setup_parsers
        from interactive_app import SPOTIFY_REQUIRED_KEYS, PlaylistInteractiveApp

        for key in SPOTIFY_REQUIRED_KEYS:
            monkeypatch.setenv(key, "test_value")
        monkeypatch.setenv("TUNR_AUTO_SYNC_MINUTES", "0")  # keep the run inert

        cli = PlaylistCLI()
        cli.last_search_results = [dict(r) for r in RESULTS]

        async def drive():
            app = PlaylistInteractiveApp(cli=cli, parser=setup_parsers())
            async with app.run_test(size=(100, 30)) as pilot:
                app._open_results()
                await pilot.pause()
                assert isinstance(app.screen, ResultsScreen)
                await pilot.press("enter")
                await pilot.pause()
                assert not isinstance(app.screen, ResultsScreen)
                value = app.query_one("#command_input", Input).value
                assert value == '/find "more like Aligned by Result Artist"'
                assert app.status == "idle"  # inserted, never dispatched

        # on_mount replaces root logging handlers and points the ui sinks at
        # this app; restore both afterwards (same pattern as the Esc pilot).
        root = _logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        try:
            _drive(drive())
        finally:
            root.handlers = saved_handlers
            root.setLevel(saved_level)
            _ui.set_output_sink(None)
            _ui.set_preview_sink(None)
            _ui.set_status_sink(None)


# ---------------------------------------------------------------------------
# /results and /browse meta-command routing
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_history(monkeypatch, tmp_path):
    """App __init__ resolves the history path; keep tests off real data/."""
    monkeypatch.setenv("TUNR_HISTORY_PATH", str(tmp_path / "tunr_history"))


def _make_app(monkeypatch):
    from arg_parse import setup_parsers
    from interactive_app import SPOTIFY_REQUIRED_KEYS, PlaylistInteractiveApp

    for key in SPOTIFY_REQUIRED_KEYS:
        monkeypatch.setenv(key, "test_value")

    class DummyApp(PlaylistInteractiveApp):
        def __init__(self, cli, parser):
            super().__init__(cli=cli, parser=parser)
            self.logged = []
            self.commands = []
            self.results_opened = 0

        def append_log(self, renderable) -> None:
            self.logged.append(renderable)

        def _run_command(self, command, args) -> None:
            self.commands.append(command)

        def _open_results(self) -> None:
            self.results_opened += 1

    app = DummyApp(cli=PlaylistCLI(), parser=setup_parsers())
    app._refresh_env_status()
    return app


def _logged_text(app, width: int = 100) -> str:
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    console = Console(file=buf, width=width)
    for renderable in app.logged:
        console.print(renderable)
    return buf.getvalue()


class TestResultsMetaCommand:
    def test_results_routes_to_browser(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/results")
        assert app.results_opened == 1
        assert app.commands == []  # never dispatched to argparse

    def test_browse_alias(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/browse")
        assert app.results_opened == 1

    def test_results_listed_in_help(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help")
        assert "/results" in _logged_text(app)

    def test_help_results_shows_meta_help(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help results")
        assert "Browse the last /search or /find results" in _logged_text(app)
