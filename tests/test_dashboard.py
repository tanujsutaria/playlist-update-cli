"""Tests for the /dash dashboard (src/dashboard.py + the /dash meta-command).

Covers: the pure data providers against a seeded in-memory SQLite (including
the sub-30s play-rule exclusion), range-cutoff math, the InteractiveBarChart
render/selection behavior (offline, no running app), tab cycling on the
screen, and the interactive_app routing for /dash. All offline.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from dashboard import (
    CLOCK_MIN_PLAYS,
    DAY_BARS_MIN_DAYS,
    DAY_BARS_WINDOW,
    RANGE_KEYS,
    SHARE_MIN_PLAYS,
    DashboardScreen,
    InteractiveBarChart,
    plays_data,
    range_cutoff,
    stats_data,
    tab_data,
    taste_data,
)
from storage.migrations import ensure_schema
from storage.repos import Repositories
from ui import ACCENT_BLUE, ACCENT_GREEN, ACCENT_ORANGE

NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Seeded stub cli (only the .repos slice the providers need)
# ---------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn


def _seed_track(
    conn: sqlite3.Connection,
    artist: str,
    name: str,
    spotify_id=None,
    fields=None,
    embedded: bool = False,
) -> str:
    track_id = f"{artist.lower()}|||{name.lower()}"
    conn.execute(
        "INSERT OR IGNORE INTO artists (artist_id, name) VALUES (?, ?);",
        (artist.lower(), artist),
    )
    conn.execute(
        "INSERT OR IGNORE INTO tracks (track_id, name, artist_id, spotify_id) VALUES (?, ?, ?, ?);",
        (track_id, name, artist.lower(), spotify_id),
    )
    if fields is not None:
        conn.execute(
            "INSERT INTO track_context (track_id, fields_json) VALUES (?, ?);",
            (track_id, json.dumps(fields)),
        )
    if embedded:
        conn.execute(
            "INSERT INTO track_embeddings (track_id, model_name, embedding_blob, embedding_dim)"
            " VALUES (?, ?, ?, ?);",
            (track_id, "stub-model", b"\x00\x00\x00\x00", 1),
        )
    return track_id


def _add_event(conn, event_id, track_id, played_at, ms_played=None) -> None:
    conn.execute(
        "INSERT INTO listen_events (event_id, track_id, played_at, source, created_at, ms_played)"
        " VALUES (?, ?, ?, 'recently_played', ?, ?);",
        (event_id, track_id, played_at, played_at, ms_played),
    )


def _field(name, value):
    return {"field": name, "value": value}


@pytest.fixture
def seeded_cli():
    """A stub cli over a seeded temp DB: 3 enriched tracks, 5 listen events
    (one sub-30s event that the play rule must exclude)."""
    conn = _connect()
    hot = _seed_track(
        conn,
        "Artist A",
        "hot song",
        spotify_id="sp1",
        embedded=True,
        fields=[
            _field("mood", "dreamy, hazy"),
            _field("genres", "shoegaze, dream pop"),
            _field("era", "2010s"),
        ],
    )
    cold = _seed_track(
        conn,
        "Artist B",
        "cold song",
        spotify_id="sp2",
        fields=[
            _field("moods", "dreamy"),
            _field("genre", "shoegaze"),
            _field("era", "1990s"),
        ],
    )
    _seed_track(
        conn,
        "Artist C",
        "third song",
        fields=[
            _field("mood", "upbeat"),
            _field("genre", "synth-pop"),
            _field("era", "2016"),
        ],
    )
    # hot: three counted plays (8d, 2d, 1d before NOW), spread across day parts.
    _add_event(conn, "e1", hot, "2026-06-02T10:00:00Z")  # morning, outside 7d
    _add_event(conn, "e2", hot, "2026-06-08T20:00:00Z", ms_played=45_000)  # evening
    _add_event(conn, "e3", hot, "2026-06-09T13:00:00Z")  # afternoon
    # cold: one counted play far in the past + one sub-30s event (never counts).
    _add_event(conn, "old", cold, "2026-03-02T03:00:00Z")  # night, outside 90d
    _add_event(conn, "skip", cold, "2026-06-09T14:00:00Z", ms_played=5_000)
    return SimpleNamespace(repos=Repositories(conn))


@pytest.fixture
def empty_cli():
    return SimpleNamespace(repos=Repositories(_connect()))


# ---------------------------------------------------------------------------
# range_cutoff
# ---------------------------------------------------------------------------


class TestRangeCutoff:
    def test_all_means_no_cutoff(self):
        assert range_cutoff("all", now=NOW) is None

    def test_day_ranges(self):
        assert range_cutoff("7d", now=NOW) == "2026-06-03T12:00:00Z"
        assert range_cutoff("30d", now=NOW) == "2026-05-11T12:00:00Z"
        assert range_cutoff("90d", now=NOW) == "2026-03-12T12:00:00Z"

    def test_naive_now_is_treated_as_utc(self):
        naive = datetime(2026, 6, 10, 12, 0, 0)
        assert range_cutoff("7d", now=naive) == "2026-06-03T12:00:00Z"

    def test_unknown_key_means_no_cutoff(self):
        assert range_cutoff("nonsense", now=NOW) is None

    def test_every_cycling_key_is_handled(self):
        for key in RANGE_KEYS:
            cutoff = range_cutoff(key, now=NOW)
            assert (cutoff is None) == (key == "all")


# ---------------------------------------------------------------------------
# taste_data
# ---------------------------------------------------------------------------


class TestTasteData:
    def test_top_moods_and_genres(self, seeded_cli):
        rows, caption = taste_data(seeded_cli)
        by_label = {(d["group"], label): value for label, value, d in rows}
        # "dreamy" is tagged under both raw names (mood + moods) -> 2 tracks.
        assert by_label[("moods", "dreamy")] == 2.0
        assert by_label[("genres", "shoegaze")] == 2.0
        assert ("moods", "upbeat") in by_label
        assert "curated core · 3 enriched tracks" in caption
        assert "/enrich" in caption

    def test_share_uses_enriched_denominator(self, seeded_cli):
        rows, _ = taste_data(seeded_cli)
        detail = next(d for label, _, d in rows if label == "dreamy")
        assert detail["share"] == "67%"  # 2 of 3 enriched tracks
        assert detail["extra"] == "mood · 2 of 3 enriched tracks"

    def test_mirror_rows_never_dilute_shares(self, seeded_cli):
        """Raw library-mirror rows (tracks with NO context) must not change a
        single facet count, share, or the named denominator — the taste tab
        scopes to the curated core deliberately."""
        before, _ = taste_data(seeded_cli)
        conn = seeded_cli.repos.conn
        for i in range(50):  # a mirror wave dwarfing the 3-track curated core
            _seed_track(conn, f"Mirror Artist {i}", f"mirror song {i}", spotify_id=f"m{i}")
        after, caption = taste_data(seeded_cli)
        assert after == before  # counts AND shares identical
        assert "curated core · 3 enriched tracks" in caption  # denominator unmoved
        detail = next(d for label, _, d in after if label == "dreamy")
        assert detail["share"] == "67%"  # of enriched, never of the 53-row mirror

    def test_empty_library(self, empty_cli):
        rows, _ = taste_data(empty_cli)
        assert rows == []


# ---------------------------------------------------------------------------
# stats_data
# ---------------------------------------------------------------------------


class TestStatsData:
    def test_decades_and_coverage(self, seeded_cli):
        rows, caption = stats_data(seeded_cli)
        by_label = {(d["group"], label): value for label, value, d in rows}
        # 2010s + 2016 both bucket to the 2010s; 1990s is its own decade.
        assert by_label[("decades", "2010s")] == 2.0
        assert by_label[("decades", "1990s")] == 1.0
        # Coverage joins live tracks: 3 contexts, 1 embedding, 0 sonic, 2 ids.
        assert by_label[("coverage", "context")] == 3.0
        assert by_label[("coverage", "embeddings")] == 1.0
        assert by_label[("coverage", "sonic")] == 0.0
        assert by_label[("coverage", "spotify id")] == 2.0
        assert "3/3 enriched tracks datable" in caption

    def test_scope_labels_name_their_denominators(self, seeded_cli):
        """Every stats row names its population: decades are curated-core
        claims, coverage rows are mirror claims, and the caption carries the
        mirror scope + the /enrich growth pointer."""
        rows, caption = stats_data(seeded_cli)
        decade_extra = next(d["extra"] for _, _, d in rows if d["group"] == "decades")
        assert decade_extra == "of 3 datable tracks · curated core"
        context_extra = next(d["extra"] for label, _, d in rows if label == "context")
        assert context_extra == "3 of 3 mirror tracks have it"
        assert "coverage vs the library mirror · 3 tracks · 100% enriched" in caption
        assert "/enrich grows this" in caption

    def test_empty_library(self, empty_cli):
        rows, _ = stats_data(empty_cli)
        assert rows == []


# ---------------------------------------------------------------------------
# plays_data
# ---------------------------------------------------------------------------


@pytest.fixture
def grown_cli():
    """A ledger past every small-N threshold: 210 counted plays spread over
    7 UTC days (and every day part), all on one track."""
    conn = _connect()
    hot = _seed_track(conn, "Artist A", "hot song", spotify_id="sp1")
    event = 0
    for day in range(1, 8):  # 2026-06-01 .. 2026-06-07
        for k in range(30):  # 30 plays per day -> 210 counted
            _add_event(conn, f"g{event}", hot, f"2026-06-{day:02d}T{(k * 5) % 24:02d}:10:00Z")
            event += 1
    return SimpleNamespace(repos=Repositories(conn))


class TestPlaysData:
    def test_sub_30s_events_never_count(self, seeded_cli):
        rows, _ = plays_data(seeded_cli, "all", now=NOW)
        tracks = {label: value for label, value, d in rows if d["group"] == "tracks"}
        assert tracks["hot song — artist a"] == 3.0
        # cold song's only counted play is the old one; the 5s skip is excluded.
        assert tracks["cold song — artist b"] == 1.0

    def test_small_ledger_earns_no_shares_no_clock_no_day_bars(self, seeded_cli):
        """4 counted plays over 4 days: top tracks only — NO share-of-listening
        percentages, no day-part clock, no per-day bars. Absent, never faked."""
        assert 4 < min(SHARE_MIN_PLAYS, CLOCK_MIN_PLAYS) and 4 < DAY_BARS_MIN_DAYS
        rows, _ = plays_data(seeded_cli, "all", now=NOW)
        assert {d["group"] for _, _, d in rows} == {"tracks"}
        assert all(d["share"] == "" for _, _, d in rows)

    def test_grown_ledger_unlocks_day_bars_clock_and_shares(self, grown_cli):
        rows, caption = plays_data(grown_cli, "all", now=NOW)
        assert {d["group"] for _, _, d in rows} == {"tracks", "days", "clock"}
        days = [(label, value) for label, value, d in rows if d["group"] == "days"]
        assert len(days) == 7
        assert days[0] == ("2026-06-01", 30.0)  # per-day counts, ascending
        assert all(d["share"] == "" for _, _, d in rows if d["group"] == "days")
        # Shares are earned at SHARE_MIN_PLAYS — and only then.
        assert next(d["share"] for _, _, d in rows if d["group"] == "tracks") == "100%"
        clock_shares = [d["share"] for _, _, d in rows if d["group"] == "clock"]
        assert clock_shares and all(s.endswith("%") for s in clock_shares)
        assert caption.startswith("210 plays since 2026-06-01")

    def test_day_bars_window_caps_at_most_recent(self):
        """A long ledger shows only the last DAY_BARS_WINDOW days of bars."""
        conn = _connect()
        hot = _seed_track(conn, "Artist A", "hot song")
        for day in range(1, 21):  # 20 days, one play each
            _add_event(conn, f"w{day}", hot, f"2026-05-{day:02d}T12:00:00Z")
        cli = SimpleNamespace(repos=Repositories(conn))
        rows, _ = plays_data(cli, "all", now=NOW)
        days = [label for label, _, d in rows if d["group"] == "days"]
        assert len(days) == DAY_BARS_WINDOW
        assert days[0] == f"2026-05-{21 - DAY_BARS_WINDOW:02d}"
        assert days[-1] == "2026-05-20"
        # 20 counted plays: day bars are earned (span), shares/clock are not.
        assert all(d["share"] == "" for _, _, d in rows)
        assert not any(d["group"] == "clock" for _, _, d in rows)

    def test_range_cutoff_filters_events(self, seeded_cli):
        rows, caption = plays_data(seeded_cli, "7d", now=NOW)
        tracks = {label: value for label, value, d in rows if d["group"] == "tracks"}
        # Only the 2d and 1d plays fall inside the 7d window.
        assert tracks == {"hot song — artist a": 2.0}
        assert caption.startswith("2 plays since 2026-06-03 · grows via /listen-sync")

    def test_caption_discloses_provenance(self, seeded_cli):
        _, caption = plays_data(seeded_cli, "all", now=NOW)
        # Hero number first; the ledger's start date, never lifetime listening.
        assert caption.startswith("4 plays since 2026-03-02 · grows via /listen-sync")
        assert "floor estimates" in caption
        assert "gaps while tunr closed" in caption

    def test_empty_ledger(self, empty_cli):
        rows, caption = plays_data(empty_cli, "all", now=NOW)
        assert rows == []
        assert caption.startswith("0 plays since —")
        assert "gaps while tunr closed" in caption


# ---------------------------------------------------------------------------
# InteractiveBarChart (offline: constructed, never mounted)
# ---------------------------------------------------------------------------


def _chart(rows=None, accent=ACCENT_ORANGE):
    rows = (
        rows
        if rows is not None
        else [
            ("alpha", 3.0, {"group": "g"}),
            ("beta", 2.0, {"group": "g"}),
            ("gamma", 1.0, {"group": "g"}),
        ]
    )
    return InteractiveBarChart(rows, accent=accent)


class TestInteractiveBarChart:
    def test_render_contains_labels_and_selected_marker(self):
        chart = _chart()
        lines = chart.render().plain.splitlines()
        assert len(lines) == 3
        assert lines[0].startswith("▸")
        assert "alpha" in lines[0]
        assert "beta" in lines[1]
        assert not lines[1].startswith("▸")
        # Bars come from ui.hbar: fill glyphs + the dim track remainder.
        assert "█" in lines[0]
        assert "╌" in lines[2]  # the smallest bar still shows its full scale

    def test_marker_follows_selection(self):
        chart = _chart()
        chart.selected = 2
        lines = chart.render().plain.splitlines()
        assert not lines[0].startswith("▸")
        assert lines[2].startswith("▸")

    def test_cursor_actions_clamp_at_bounds(self):
        chart = _chart()
        chart.action_cursor_up()
        assert chart.selected == 0  # clamped at the top
        chart.action_cursor_down()
        chart.action_cursor_down()
        assert chart.selected == 2
        chart.action_cursor_down()
        assert chart.selected == 2  # clamped at the bottom

    def test_click_selects_row_from_y(self):
        chart = _chart()
        chart.on_click(SimpleNamespace(y=1, stop=lambda: None))
        assert chart.selected == 1

    def test_click_clamps_below_last_row(self):
        chart = _chart()
        chart.on_click(SimpleNamespace(y=99, stop=lambda: None))
        assert chart.selected == 2

    def test_set_rows_resets_selection_and_accent(self):
        chart = _chart()
        chart.selected = 2
        chart.set_rows([("only", 1.0, {})], accent=ACCENT_GREEN, empty_message="nothing")
        assert chart.selected == 0
        assert chart.accent == ACCENT_GREEN

    def test_empty_rows_render_empty_message(self):
        chart = InteractiveBarChart([], empty_message="no plays yet — run /listen-sync")
        assert chart.render().plain == "no plays yet — run /listen-sync"
        # Selection on an empty chart pins to 0 and never raises.
        chart.action_cursor_down()
        assert chart.selected == 0

    def test_render_never_soft_wraps(self):
        """on_click maps event.y -> row index, so row i must render on line i
        even when the terminal is narrower than label+bar+value (the bar is
        floored at 8 cells): the Text must be no-wrap/crop, never wrapped."""
        chart = _chart([("a very long label that overflows", 3.0, {"group": "g"})])
        rendered = chart.render()
        assert rendered.no_wrap is True
        assert rendered.overflow == "crop"


# ---------------------------------------------------------------------------
# DashboardScreen: tab/range cycling (headless — _refresh_data stubbed out)
# ---------------------------------------------------------------------------


class HeadlessDashboard(DashboardScreen):
    """Records refreshes instead of touching unmounted widgets."""

    def __init__(self, cli):
        super().__init__(cli)
        self.refreshes = []

    def _refresh_data(self) -> None:
        self.refreshes.append((self.active_tab, self.range_key))


class TestDashboardScreen:
    def test_tab_cycling_wraps_both_ways(self, empty_cli):
        screen = HeadlessDashboard(empty_cli)
        assert screen.active_tab == "taste"
        screen.action_next_tab()
        assert screen.active_tab == "stats"
        screen.action_next_tab()
        assert screen.active_tab == "plays"
        screen.action_next_tab()
        assert screen.active_tab == "taste"  # wraps
        screen.action_prev_tab()
        assert screen.active_tab == "plays"  # wraps backwards
        assert len(screen.refreshes) == 4

    def test_range_cycles_only_on_plays_tab(self, empty_cli):
        screen = HeadlessDashboard(empty_cli)
        screen.action_cycle_range()  # taste tab: no-op
        assert screen.range_key == "all"
        assert screen.refreshes == []
        screen.tab_index = screen.TABS.index("plays")
        for expected in ("90d", "30d", "7d", "all"):
            screen.action_cycle_range()
            assert screen.range_key == expected

    def test_tab_accents_follow_op1_color_ownership(self):
        assert DashboardScreen.TAB_ACCENTS == {
            "taste": ACCENT_BLUE,
            "stats": ACCENT_GREEN,
            "plays": ACCENT_ORANGE,
        }

    def test_tab_cycling_switches_provider(self, seeded_cli):
        """Each tab dispatches to a different provider (distinct row groups)."""
        groups_by_tab = {}
        for tab in DashboardScreen.TABS:
            rows, _ = tab_data(seeded_cli, tab, "all")
            groups_by_tab[tab] = {d["group"] for _, _, d in rows}
        assert groups_by_tab["taste"] == {"moods", "genres"}
        assert groups_by_tab["stats"] == {"decades", "coverage"}
        # A 4-play ledger has earned no clock/day-bar panels — tracks only.
        assert groups_by_tab["plays"] == {"tracks"}

    def test_per_tab_empty_states(self):
        messages = DashboardScreen.EMPTY_MESSAGES
        assert set(messages) == set(DashboardScreen.TABS)
        assert "/listen-sync" in messages["plays"]
        assert "re-auth" in messages["plays"]
        assert "/enrich" in messages["taste"]


# ---------------------------------------------------------------------------
# Pilot smoke: push the screen, drive the keys, dismiss (~0.3s warm)
# ---------------------------------------------------------------------------


class TestPilotSmoke:
    def test_push_drive_dismiss(self, seeded_cli):
        import asyncio

        from textual.app import App

        class SmokeApp(App):
            pass

        async def drive():
            app = SmokeApp()
            async with app.run_test(size=(90, 30)) as pilot:
                screen = DashboardScreen(seeded_cli)
                await app.push_screen(screen)
                await pilot.pause()
                assert app.screen is screen
                chart = screen.query_one(InteractiveBarChart)
                assert chart.rows  # taste tab has seeded data
                rendered = chart.render().plain
                assert "dreamy" in rendered and "▸" in rendered
                # Selection moves on the (time-independent) taste tab.
                await pilot.press("down")
                assert chart.selected == 1
                # Tabs cycle; r cycles the plays range only once we're there.
                await pilot.press("right")
                assert screen.active_tab == "stats"
                await pilot.press("r")
                assert screen.range_key == "all"  # no-op off the plays tab
                await pilot.press("right", "r")
                assert screen.active_tab == "plays"
                assert screen.range_key == "90d"
                await pilot.press("escape")
                await pilot.pause()
                assert app.screen is not screen  # dismissed

        try:
            asyncio.run(drive())
        finally:
            # py3.9: asyncio.run() leaves the main thread with *no* current
            # event loop (set_event_loop was called, so get_event_loop no
            # longer auto-creates and raises RuntimeError). Restore one so
            # later headless Textual tests (e.g. test_interactive_app's
            # compose helpers) keep working regardless of ordering.
            asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------------------
# /dash meta-command routing in the interactive app
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_history(monkeypatch, tmp_path):
    """App __init__ resolves the history path; keep tests off real data/."""
    monkeypatch.setenv("TUNR_HISTORY_PATH", str(tmp_path / "tunr_history"))


def _make_app(monkeypatch):
    from arg_parse import setup_parsers
    from interactive_app import SPOTIFY_REQUIRED_KEYS, PlaylistInteractiveApp
    from main import PlaylistCLI

    for key in SPOTIFY_REQUIRED_KEYS:
        monkeypatch.setenv(key, "test_value")

    class DummyApp(PlaylistInteractiveApp):
        def __init__(self, cli, parser):
            super().__init__(cli=cli, parser=parser)
            self.logged = []
            self.commands = []
            self.dashboard_opened = 0

        def append_log(self, renderable) -> None:
            self.logged.append(renderable)

        def _run_command(self, command, args) -> None:
            self.commands.append(command)

        def _open_dashboard(self) -> None:
            self.dashboard_opened += 1

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


class TestDashMetaCommand:
    def test_dash_routes_to_dashboard(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/dash")
        assert app.dashboard_opened == 1
        assert app.commands == []  # never dispatched to argparse

    def test_dashboard_alias(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/dashboard")
        assert app.dashboard_opened == 1

    def test_dash_listed_in_help(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help")
        assert "/dash" in _logged_text(app)

    def test_help_dash_shows_meta_help(self, monkeypatch):
        app = _make_app(monkeypatch)
        app._handle_command("/help dash")
        assert "dashboard" in _logged_text(app)
