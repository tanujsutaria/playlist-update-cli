"""The /dash interactive dashboard: taste · stats · plays, OP-1 styled.

Three layers, deliberately separable:

* **Data providers** (`taste_data` / `stats_data` / `plays_data`) are pure
  functions over ``cli.repos.conn`` — no Textual, no Rich emission — so every
  number on the dashboard is unit-testable against a seeded SQLite. Each
  returns ``(rows, caption)`` where rows are ``(label, value, detail)`` and
  the caption is the provenance footnote (the honesty doctrine: plays are
  floor estimates with gaps while tunr was closed — the caption says so).
* **InteractiveBarChart** is a focusable widget whose ``render()`` reuses the
  `ui.hbar` builder (builders return Text and never emit) — one bar line per
  row, the selected row restyled, up/down/click selection.
* **DashboardScreen** owns the chrome: lowercase tab strip with OP-1
  color-ownership (taste=blue, stats=green, plays=orange), the detail
  readout, footer key hints, and per-tab empty states.

Data is recomputed synchronously on mount and on tab/range change — the
providers are COUNT/GROUP BY queries plus one pass over the ~1.2k enriched
context rows, well under any latency that would warrant a worker even with
the ~15k-row library mirror underneath.

Scope doctrine (see `scopes.py`): taste facets aggregate the CURATED CORE
only — mirror rows never dilute a share — coverage panels name the mirror
denominator explicitly, and plays captions lead with the counted-play hero
number and the ledger's start date (never lifetime listening).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import ClassVar, Dict, List, Optional, Protocol, Sequence, Tuple

from rich.text import Text
from textual import events
from textual.app import ComposeResult, RenderResult
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Static

from plays import daily_counts, listening_clock, play_counts, plays_meta, top_played
from scopes import curated_scope_caption, fmt_n, library_scopes, mirror_scope_caption
from storage.repos import Repositories
from taste_facets import decade_histogram, facet_track_counts, fold_genre, taste_title
from ui import ACCENT_BLUE, ACCENT_GREEN, ACCENT_ORANGE, ACCENT_WHITE, CAPTION_STYLE, hbar


class SupportsRepos(Protocol):
    """The slice of PlaylistCLI the dashboard needs (keeps this module
    decoupled from main.py and strictly typed)."""

    @property
    def repos(self) -> Repositories: ...


# A chart row: (label, value, detail). The detail dict carries optional
# pre-computed strings for the readout: "group" (which sub-series the row
# belongs to), "share" (e.g. "38%" — the provider knows the honest
# denominator), and "extra" (free-text tail).
Detail = Dict[str, str]
Row = Tuple[str, float, Detail]
TabData = Tuple[List[Row], str]

# Plays ranges, in r-key cycling order. "all" means no cutoff.
RANGE_KEYS: Tuple[str, ...] = ("all", "90d", "30d", "7d")
_RANGE_DAYS: Dict[str, int] = {"90d": 90, "30d": 30, "7d": 7}

# Small-N honesty gates for the plays tab — thresholds, not hardcoded
# smallness, so the view grows panels as the ledger accrues instead of ever
# implying signal a ~50-event window cannot carry:
#   * below SHARE_MIN_PLAYS counted plays, NO share-of-listening percentages
#     anywhere (a 3-play "6%" is noise wearing a suit);
#   * below CLOCK_MIN_PLAYS, no day-part listening clock;
#   * per-day bars appear only once the ledger spans DAY_BARS_MIN_DAYS
#     distinct days, windowed to the most recent DAY_BARS_WINDOW.
SHARE_MIN_PLAYS = 200
CLOCK_MIN_PLAYS = 100
DAY_BARS_MIN_DAYS = 5
DAY_BARS_WINDOW = 14


def range_cutoff(range_key: str, now: Optional[datetime] = None) -> Optional[str]:
    """The ``played_at >= ?`` cutoff for a range key, as UTC ISO-8601 'Z'.

    ``"all"`` (and any unknown key) means no cutoff -> None. Pass ``now`` for
    deterministic tests; a naive ``now`` is treated as UTC.
    """
    days = _RANGE_DAYS.get(range_key)
    if days is None:
        return None
    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    cutoff = (reference - timedelta(days=days)).astimezone(timezone.utc)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pct(part: float, whole: float) -> str:
    """A share string for the detail readout; '—' when the denominator is 0."""
    if whole <= 0:
        return "—"
    return f"{part / whole * 100:.0f}%"


def _context_pairs(conn: sqlite3.Connection) -> List[Tuple[str, str]]:
    """(track_id, fields_json) for enriched LIVE tracks — JOIN-scoped so an
    orphan context row (track deleted) never inflates a facet count."""
    rows = conn.execute(
        "SELECT tc.track_id AS track_id, tc.fields_json AS fields_json "
        "FROM track_context tc JOIN tracks t ON t.track_id = tc.track_id;"
    ).fetchall()
    return [(str(row[0]), str(row[1] or "")) for row in rows]


def taste_data(cli: SupportsRepos, limit: int = 6) -> TabData:
    """Top moods + top genres (tracks tagged), via the taste_facets counters.

    Scoped to the CURATED CORE deliberately: `_context_pairs` only returns
    enriched tracks, so raw library-mirror rows can never dilute a facet
    share, and every share names that denominator.
    """
    conn = cli.repos.conn
    pairs = _context_pairs(conn)
    enriched = len(pairs)
    mood_counts = facet_track_counts(pairs, "moods")
    genre_counts = facet_track_counts(pairs, "genres", fold=fold_genre)
    rows: List[Row] = []
    for group, counts in (("moods", mood_counts), ("genres", genre_counts)):
        for label, count in counts[:limit]:
            rows.append(
                (
                    label,
                    float(count),
                    {
                        "group": group,
                        "share": _pct(count, enriched),
                        "extra": f"{group[:-1]} · {count} of {fmt_n(enriched)} enriched tracks",
                    },
                )
            )
    scopes = library_scopes(conn)
    caption = f"{curated_scope_caption(scopes)} · tags from /enrich — semantic, not acoustic"
    headline = taste_title(mood_counts, genre_counts)
    if headline:
        caption = f"{headline} · {caption}"
    return rows, caption


def stats_data(cli: SupportsRepos, limit: int = 8) -> TabData:
    """Era histogram (decades) + data-coverage counts over live tracks."""
    conn = cli.repos.conn
    pairs = _context_pairs(conn)
    decades, datable, unbucketable = decade_histogram(pairs)
    total = int(conn.execute("SELECT COUNT(*) FROM tracks;").fetchone()[0])
    if total == 0:
        return [], "library is empty"

    def _join_count(side_table: str) -> int:
        return int(
            conn.execute(
                f"SELECT COUNT(*) FROM tracks t JOIN {side_table} x ON x.track_id = t.track_id;"
            ).fetchone()[0]
        )

    rows: List[Row] = []
    for label, count in decades[:limit]:
        rows.append(
            (
                label,
                float(count),
                {
                    "group": "decades",
                    "share": _pct(count, datable),
                    "extra": f"of {datable} datable tracks · curated core",
                },
            )
        )
    coverage: List[Tuple[str, int]] = [
        ("context", _join_count("track_context")),
        ("embeddings", _join_count("track_embeddings")),
        ("sonic", _join_count("track_sonic")),
        (
            "spotify id",
            int(
                conn.execute(
                    "SELECT COUNT(*) FROM tracks WHERE spotify_id IS NOT NULL AND spotify_id != '';"
                ).fetchone()[0]
            ),
        ),
    ]
    for label, have in coverage:
        rows.append(
            (
                label,
                float(have),
                {
                    "group": "coverage",
                    "share": _pct(have, total),
                    "extra": f"{fmt_n(have)} of {fmt_n(total)} mirror tracks have it",
                },
            )
        )
    scopes = library_scopes(conn)
    caption = (
        f"{datable}/{len(pairs)} enriched tracks datable · {unbucketable} defied parsing"
        f" · coverage vs the {mirror_scope_caption(scopes)}"
    )
    return rows, caption


def plays_data(
    cli: SupportsRepos,
    range_key: str = "all",
    limit: int = 10,
    now: Optional[datetime] = None,
) -> TabData:
    """Top-played tracks for one range, plus panels the ledger has EARNED.

    Counts follow the canonical play rule (sub-30s events never count) and
    are FLOOR estimates: ``recently_played`` polling only sees what played
    while tunr was open — the caption discloses exactly that, leads with the
    hero number, and names the window start (never lifetime listening).

    Small-N honesty (the ledger starts near-empty and only grows via
    /listen-sync polling): share-of-listening percentages appear only at
    ``SHARE_MIN_PLAYS`` counted plays, per-day bars only once the ledger
    spans ``DAY_BARS_MIN_DAYS`` days, and the day-part clock only at
    ``CLOCK_MIN_PLAYS`` — below each threshold the panel is absent, never
    faked.
    """
    conn = cli.repos.conn
    since = range_cutoff(range_key, now=now)
    counted = sum(play_counts(conn, since=since).values())
    show_shares = counted >= SHARE_MIN_PLAYS
    rows: List[Row] = []
    for track in top_played(conn, limit=limit, since=since):
        name = str(track["name"] or track["track_id"])
        artist = str(track["artist"] or "")
        label = f"{name} — {artist}".strip(" —").lower()
        last_played = str(track["last_played"] or "")[:10]
        rows.append(
            (
                label,
                float(track["plays"]),
                {
                    "group": "tracks",
                    "share": _pct(float(track["plays"]), counted) if show_shares else "",
                    "extra": f"last played {last_played}" if last_played else "",
                },
            )
        )
    daily = daily_counts(conn, since=since)
    if len(daily) >= DAY_BARS_MIN_DAYS:
        for day, day_plays in daily[-DAY_BARS_WINDOW:]:
            rows.append(
                (
                    day,
                    float(day_plays),
                    {
                        "group": "days",
                        "share": "",
                        "extra": "plays that utc day · gap days not drawn",
                    },
                )
            )
    if counted >= CLOCK_MIN_PLAYS:
        clock = listening_clock(conn, since=since)
        clock_total = sum(clock)
        day_parts: Sequence[Tuple[str, int, int]] = (
            ("night 00-06", 0, 6),
            ("morning 06-12", 6, 12),
            ("afternoon 12-18", 12, 18),
            ("evening 18-24", 18, 24),
        )
        for label, start, stop in day_parts:
            bucket = sum(clock[start:stop])
            rows.append(
                (
                    label,
                    float(bucket),
                    {
                        "group": "clock",
                        "share": _pct(bucket, clock_total) if show_shares else "",
                        "extra": "plays by utc hour",
                    },
                )
            )
    meta = plays_meta(conn)
    if since is not None:
        since_date = since[:10]
    else:
        since_date = str(meta["first_played_at"] or "")[:10] or "—"
    caption = (
        f"{counted} plays since {since_date} · grows via /listen-sync"
        f" · ≥30s plays only, floor estimates · gaps while tunr closed"
    )
    return rows, caption


def tab_data(
    cli: SupportsRepos,
    tab: str,
    range_key: str = "all",
    now: Optional[datetime] = None,
) -> TabData:
    """Dispatch a tab name to its data provider (the screen's single seam)."""
    if tab == "taste":
        return taste_data(cli)
    if tab == "stats":
        return stats_data(cli)
    return plays_data(cli, range_key, now=now)


class InteractiveBarChart(Widget, can_focus=True):
    """A focusable ranked-bar chart: one `ui.hbar` line per row.

    Selection moves with up/down (clamped) or click; every change posts a
    `Selected` message so the owning screen can update its detail readout.
    Pure reuse of the ink builders — no bar-drawing code lives here.
    """

    DEFAULT_CSS = """
    InteractiveBarChart {
        height: 1fr;
    }
    """

    BINDINGS: ClassVar[List[BindingType]] = [
        Binding("up", "cursor_up", "select up", show=False),
        Binding("down", "cursor_down", "select down", show=False),
    ]

    selected: reactive[int] = reactive(0)

    class Selected(Message):
        """Posted whenever the selected row changes (or rows are replaced)."""

        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(
        self,
        rows: Optional[Sequence[Row]] = None,
        *,
        accent: str = ACCENT_BLUE,
        empty_message: str = "no data",
        id: Optional[str] = None,
    ) -> None:
        super().__init__(id=id)
        self.rows: List[Row] = list(rows or [])
        self.accent: str = accent
        self.empty_message: str = empty_message

    def set_rows(self, rows: Sequence[Row], *, accent: str, empty_message: str = "no data") -> None:
        """Replace the data series (tab/range change). Resets the selection."""
        self.rows = list(rows)
        self.accent = accent
        self.empty_message = empty_message
        self.selected = 0  # validate clamps; watch may be skipped if unchanged
        self.refresh()
        self.post_message(self.Selected(self.selected))

    def _clamp(self, index: int) -> int:
        if not self.rows:
            return 0
        return max(0, min(len(self.rows) - 1, index))

    def validate_selected(self, value: int) -> int:
        return self._clamp(value)

    def watch_selected(self, value: int) -> None:
        self.refresh()
        self.post_message(self.Selected(value))

    def action_cursor_up(self) -> None:
        self.selected = self.selected - 1  # validate_selected clamps

    def action_cursor_down(self) -> None:
        self.selected = self.selected + 1  # validate_selected clamps

    def on_click(self, event: events.Click) -> None:
        """Select the row under the pointer (row i renders on line i)."""
        if not self.rows:
            return
        self.selected = int(event.y)  # validate_selected clamps
        event.stop()

    @staticmethod
    def _fmt(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else f"{value:.2f}"

    def render(self) -> RenderResult:
        if not self.rows:
            return Text(self.empty_message, style="dim")
        values = [value for _, value, _ in self.rows]
        peak = max(values)
        value_labels = [self._fmt(value) for value in values]
        value_width = max(len(label) for label in value_labels)
        label_width = 24
        total_width = self.size.width or 80
        bar_width = max(8, min(36, total_width - label_width - value_width - 6))
        # no_wrap/crop: on_click assumes row i renders on line i, so a row
        # must never soft-wrap onto a second visual line at narrow widths
        # (bar_width is floored at 8, so lines can exceed the widget width).
        out = Text(no_wrap=True, overflow="crop")
        for index, ((label, value, _detail), value_label) in enumerate(
            zip(self.rows, value_labels)
        ):
            if index:
                out.append("\n")
            is_selected = index == self.selected
            out.append("▸ " if is_selected else "  ", style=self.accent)
            label_text = Text(
                label.lower(),
                style=f"bold {ACCENT_WHITE}" if is_selected else "dim",
            )
            label_text.truncate(label_width, overflow="ellipsis", pad=True)
            out.append_text(label_text)
            out.append(" ")
            fraction = value / peak if peak > 0 else 0.0
            out.append_text(
                hbar(
                    fraction,
                    bar_width,
                    style=self.accent if is_selected else f"dim {self.accent}",
                )
            )
            out.append(" ")
            out.append(
                value_label.rjust(value_width),
                style=f"bold {self.accent}" if is_selected else "dim",
            )
        return out


class DashboardScreen(Screen[None]):
    """Full-screen dashboard: ←/→ cycles tabs, ↑/↓ selects, r cycles the
    plays range, esc/q closes. All chrome lowercase (OP-1 convention)."""

    TABS: ClassVar[Tuple[str, ...]] = ("taste", "stats", "plays")
    # OP-1 color-ownership: each tab owns exactly one accent.
    TAB_ACCENTS: ClassVar[Dict[str, str]] = {
        "taste": ACCENT_BLUE,
        "stats": ACCENT_GREEN,
        "plays": ACCENT_ORANGE,
    }
    EMPTY_MESSAGES: ClassVar[Dict[str, str]] = {
        "taste": "no enriched context yet — run /enrich",
        "stats": "library is empty — run /ingest or /search to begin",
        "plays": "no plays yet — run /listen-sync (if it 403s: /auth-reset --yes, then re-auth)",
    }

    DEFAULT_CSS = """
    DashboardScreen {
        background: $background;
        color: $foreground;
        layout: vertical;
    }
    DashboardScreen #dash_top {
        height: 1;
        padding: 0 2;
        background: $surface;
    }
    DashboardScreen #dash_chart {
        height: 1fr;
        margin: 1 2;
    }
    DashboardScreen #dash_detail {
        height: 3;
        padding: 0 2;
    }
    DashboardScreen #dash_footer {
        height: 1;
        padding: 0 2;
        background: $surface;
    }
    """

    BINDINGS: ClassVar[List[BindingType]] = [
        Binding("left", "prev_tab", "prev tab", show=False),
        Binding("right", "next_tab", "next tab", show=False),
        Binding("r", "cycle_range", "cycle range", show=False),
        Binding("escape", "close", "close", show=False),
        Binding("q", "close", "close", show=False),
    ]

    def __init__(self, cli: SupportsRepos) -> None:
        super().__init__()
        self.cli = cli
        self.tab_index: int = 0
        self.range_key: str = "all"
        self._caption: str = ""

    @property
    def active_tab(self) -> str:
        return self.TABS[self.tab_index]

    def compose(self) -> ComposeResult:
        yield Static(id="dash_top")
        yield InteractiveBarChart(id="dash_chart")
        yield Static(id="dash_detail")
        yield Static(id="dash_footer")

    def on_mount(self) -> None:
        self._refresh_data()

    # ------------------------------------------------------------------
    # Actions (bindings)
    # ------------------------------------------------------------------

    def action_next_tab(self) -> None:
        self._switch_tab(1)

    def action_prev_tab(self) -> None:
        self._switch_tab(-1)

    def _switch_tab(self, step: int) -> None:
        self.tab_index = (self.tab_index + step) % len(self.TABS)
        self._refresh_data()

    def action_cycle_range(self) -> None:
        """Cycle the plays range; a no-op on the other tabs."""
        if self.active_tab != "plays":
            return
        index = RANGE_KEYS.index(self.range_key)
        self.range_key = RANGE_KEYS[(index + 1) % len(RANGE_KEYS)]
        self._refresh_data()

    def action_close(self) -> None:
        self.dismiss(None)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _refresh_data(self) -> None:
        """Recompute the active tab's data and repaint every pane."""
        rows, caption = tab_data(self.cli, self.active_tab, self.range_key)
        self._caption = caption
        chart = self.query_one(InteractiveBarChart)
        chart.set_rows(
            rows,
            accent=self.TAB_ACCENTS[self.active_tab],
            empty_message=self.EMPTY_MESSAGES[self.active_tab],
        )
        self.query_one("#dash_top", Static).update(self._render_tab_strip())
        self.query_one("#dash_footer", Static).update(self._render_footer())
        self._update_detail()
        chart.focus()

    def _render_tab_strip(self) -> Text:
        line = Text()
        line.append("tunr dashboard", style=f"bold {ACCENT_WHITE}")
        line.append("  ")
        for index, tab in enumerate(self.TABS):
            if index:
                line.append(" · ", style="dim")
            if tab == self.active_tab:
                line.append(tab, style=f"bold {self.TAB_ACCENTS[tab]}")
            else:
                line.append(tab, style="dim")
        return line

    def _render_footer(self) -> Text:
        footer = Text("←/→ tab · ↑/↓ select · r range · esc close", style="dim")
        if self.active_tab == "plays":
            footer.append(" · ", style="dim")
            footer.append(f"range: {self.range_key}", style=ACCENT_ORANGE)
        return footer

    def on_interactive_bar_chart_selected(self, message: InteractiveBarChart.Selected) -> None:
        self._update_detail()

    def _update_detail(self) -> None:
        """The selected-row readout + the dim provenance caption below it."""
        chart = self.query_one(InteractiveBarChart)
        readout = Text()
        if chart.rows:
            label, value, detail = chart.rows[chart.selected]
            accent = self.TAB_ACCENTS[self.active_tab]
            readout.append(label.lower(), style=f"bold {ACCENT_WHITE}")
            readout.append(" — ", style="dim")
            readout.append(InteractiveBarChart._fmt(value), style=f"bold {accent}")
            share = detail.get("share")
            if share:
                readout.append(" · ", style="dim")
                readout.append(share, style=accent)
            extra = detail.get("extra")
            if extra:
                readout.append(" · ", style="dim")
                readout.append(extra.lower(), style="dim")
            readout.append("\n")
        if self._caption:
            readout.append(self._caption.lower(), style=CAPTION_STYLE)
        self.query_one("#dash_detail", Static).update(readout)
