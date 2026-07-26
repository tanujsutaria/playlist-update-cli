"""The /results browser: a cursorable DataTable over the last /search or /find.

Three layers, deliberately separable (same doctrine as dashboard.py):

* **Pure row logic** (`results_for_browse`, `rows_for_table`, `track_id_of`)
  are plain functions over the cached result dicts — no Textual, no Rich
  emission — so the table contents are unit-testable without a running app.
  A follow-up viz pass can restyle the widget without touching these.
* **ResultsAction** is the screen's dismissal payload: the app-side callback
  (interactive_app._open_results) routes it into the existing apply worker —
  or, for the ``prefill`` mode Enter produces, into the command input
  (insert-not-submit, the same convention as the command palette).
  The screen itself NEVER runs workers — while it is pushed it holds the
  app-thread read contract on the shared sqlite connection (the same
  serialized-use contract DashboardScreen documents), so all writes happen
  after dismissal, back on the app's worker machinery.
* **ResultsScreen** owns the chrome: the DataTable (row cursor), a detail
  readout fed by ``cli.debug_track`` (synchronous app-thread read), per-row
  selection markers, and an in-screen playlist-name prompt — the prompt never
  round-trips through the main app's Input or its ``_pending_action`` wizard.
  Row actions stay offline: ``c`` copies the track id (OSC-52 clipboard),
  ``o`` prints the cached Spotify link to the scrollback — never the browser,
  never the network. Rows with a resolvable Spotify identity (cached row
  first, then the local db record) render the track name as an OSC 8
  terminal hyperlink — the visible text is unchanged, so terminals without
  hyperlink support still identify the track — and the detail headline
  carries the same link.

Ordering: when the last discovery command was /find, ``cli.last_find_ranked``
holds the taste-ranked rows (a fresh /search clears it), and the browser shows
that order — the screen mirrors whatever table the user just saw. Otherwise it
shows ``last_search_results`` in relevance order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import DataTable, Input, Static

from ui import ACCENT_BLUE, ACCENT_GREEN, ACCENT_ORANGE, ACCENT_WHITE, CAPTION_STYLE, link_text

logger = logging.getLogger(__name__)

# A cached result row (from cli.last_search_results / cli.last_find_ranked).
ResultItem = Mapping[str, Any]

# Field truncation for the table: long titles/artists must not blow up column
# widths (the DataTable does not soft-wrap cells).
MAX_FIELD_CHARS = 40

SELECT_MARK = "✓"


@dataclass
class ResultsAction:
    """What the user asked the app to do with a subset of rows.

    ``mode`` mirrors the existing apply path: "db" marks the tracks accepted,
    "playlist" adds them to ``playlist_name``. The third mode, "prefill",
    carries no track ids: ``prefill`` is a ready-to-edit slash command the
    app preloads into its input (never submits) — the palette's
    insert-vs-submit convention.
    """

    mode: str
    track_ids: List[str] = field(default_factory=list)
    playlist_name: Optional[str] = None
    prefill: Optional[str] = None


def results_for_browse(cli: Any) -> List[Dict[str, Any]]:
    """The rows /results shows, in the order the user last saw them.

    ``last_find_ranked`` is set by /find (after taste re-ranking) and cleared
    by every fresh /search, so its presence means the most recent discovery
    command was /find — show the taste-ranked order. Otherwise fall back to
    the relevance-ordered ``last_search_results``.
    """
    ranked = getattr(cli, "last_find_ranked", None)
    if ranked:
        return [dict(item) for item in ranked]
    return [dict(item) for item in (getattr(cli, "last_search_results", None) or [])]


def track_id_of(item: ResultItem) -> str:
    """The cached row's track id, rebuilt from song/artist when absent."""
    track_id = item.get("track_id")
    if track_id:
        return str(track_id)
    song = str(item.get("song") or item.get("name") or "").strip()
    artist = str(item.get("artist") or "").strip()
    if song and artist:
        return f"{artist.lower()}|||{song.lower()}"
    return ""


def _clip(value: object, limit: int = MAX_FIELD_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _fmt_score(item: ResultItem) -> str:
    """Score column: /search rows carry ``score``; /find rows carry ``blended``."""
    for key in ("score", "blended"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return f"{float(value):.3f}"
    return "—"


def _fmt_metric(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}" if not float(value).is_integer() else str(int(value))
    return _clip(value, 16)


def metric_names(results: Sequence[ResultItem]) -> List[str]:
    """The union of metric keys across rows, in first-seen order."""
    names: List[str] = []
    for item in results:
        metrics = item.get("metrics") or {}
        if isinstance(metrics, Mapping):
            for key in metrics:
                if key not in names:
                    names.append(str(key))
    return names


def rows_for_table(results: Sequence[ResultItem]) -> Tuple[List[str], List[List[str]]]:
    """(headers, rows) for the browser table — pure, offline, no widgets.

    Handles empty input, rows with missing metrics (blank cells), and long
    fields (clipped). The selection-marker column is screen chrome and is NOT
    part of this contract.
    """
    if not results:
        return [], []
    metrics = metric_names(results)
    headers = ["#", "song", "artist", "year", "score"] + metrics
    rows: List[List[str]] = []
    for index, item in enumerate(results, 1):
        row = [
            str(index),
            _clip(item.get("song") or item.get("name") or ""),
            _clip(item.get("artist") or ""),
            str(item.get("year") or "—"),
            _fmt_score(item),
        ]
        item_metrics = item.get("metrics") or {}
        if not isinstance(item_metrics, Mapping):
            item_metrics = {}
        row.extend(_fmt_metric(item_metrics.get(name)) for name in metrics)
        rows.append(row)
    return headers, rows


def spotify_url_for_item(item: ResultItem) -> str:
    """A clickable URL from the cached row itself (no db, no network)."""
    url = str(item.get("spotify_url") or "")
    if url:
        return url
    uri = str(item.get("spotify_uri") or "")
    if uri.startswith("http"):
        return uri
    if uri.startswith("spotify:track:"):
        tail = uri.split(":")[-1]
        if tail:
            return f"https://open.spotify.com/track/{tail}"
    return ""


def resolve_spotify_url(cli: Any, item: ResultItem) -> str:
    """The row's Spotify URL, fully offline: the cached row first
    (`spotify_url_for_item`), then the local db record via the duck-typed
    ``cli.spotify_url_for_track`` — never the network. "" when neither side
    knows a Spotify identity; a failing db lookup degrades to "" too.
    """
    url = spotify_url_for_item(item)
    if url:
        return url
    track_id = track_id_of(item)
    lookup = getattr(cli, "spotify_url_for_track", None)
    if track_id and callable(lookup):
        try:
            return str(lookup(track_id) or "")
        except Exception:
            logger.debug("spotify url lookup failed for %s", track_id, exc_info=True)
    return ""


def prefill_for_item(item: ResultItem) -> str:
    """The editable slash command Enter hands back to the app for one row.

    ``/find`` is the natural default action over a result row — "find more
    like this" continues the discovery loop, and its freeform query is the
    identifier every user can read and edit. The query is double-quoted for
    the app's ``shlex.split`` so titles with apostrophes survive parsing.
    Empty when the row carries nothing usable (no song/artist, no track id).
    """
    song = str(item.get("song") or item.get("name") or "").strip()
    artist = str(item.get("artist") or "").strip()
    if not (song or artist):
        track_id = str(item.get("track_id") or "")
        if "|||" in track_id:
            artist, _, song = (part.strip() for part in track_id.partition("|||"))
    if song and artist:
        seed = f"more like {song} by {artist}"
    elif song or artist:
        seed = f"more like {song or artist}"
    else:
        return ""
    quoted = seed.replace("\\", "\\\\").replace('"', '\\"')
    return f'/find "{quoted}"'


class ResultsScreen(Screen[Optional[ResultsAction]]):
    """Row-level browser over the cached results: ↑/↓ cursor, enter prefill
    a /find seeded with the row (insert-not-submit), i inspect, space select,
    c copy the track id, o print the Spotify link, a accept selected,
    p playlist selected, esc/q close. All reads run synchronously on the app
    thread (the pushed screen holds the serialized-connection contract); the
    dismissal payload carries the write/prefill intent back to the app, which
    owns the workers and the command input.
    """

    DEFAULT_CSS = """
    ResultsScreen {
        background: $background;
        color: $foreground;
        layout: vertical;
    }
    ResultsScreen #results_top {
        height: 1;
        padding: 0 2;
        background: $surface;
    }
    ResultsScreen #results_table {
        height: 1fr;
        margin: 0 1;
    }
    ResultsScreen #results_detail {
        height: 4;
        padding: 0 2;
    }
    ResultsScreen #results_prompt {
        display: none;
        margin: 0 2;
    }
    ResultsScreen #results_footer {
        height: 1;
        padding: 0 2;
        background: $surface;
    }
    """

    BINDINGS: ClassVar[List[BindingType]] = [
        Binding("escape", "close", "close", show=False),
        Binding("q", "close", "close", show=False),
        Binding("space", "toggle_select", "select", show=False),
        Binding("i", "inspect_row", "inspect", show=False),
        Binding("c", "copy_track_id", "copy track id", show=False),
        Binding("o", "spotify_url", "print spotify link", show=False),
        Binding("a", "accept_selected", "accept selected", show=False),
        Binding("p", "playlist_selected", "add to playlist", show=False),
    ]

    def __init__(self, cli: Any) -> None:
        super().__init__()
        self.cli = cli
        self.results: List[Dict[str, Any]] = results_for_browse(cli)
        self.selected: Set[int] = set()
        # Resolved once up front (offline: cached row, then local db) so the
        # table build, the detail headline, and the `o` action all agree on
        # each row's link without repeating db reads on every cursor move.
        self.row_urls: List[str] = [resolve_spotify_url(cli, item) for item in self.results]

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="results_top")
        yield DataTable(id="results_table")
        yield Static(id="results_detail")
        yield Input(placeholder="playlist name — enter to add, esc to cancel", id="results_prompt")
        yield Static(id="results_footer")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        headers, rows = rows_for_table(self.results)
        if rows:
            table.add_column(" ", key="_selected")
            for header in headers:
                table.add_column(header)
            for index, row in enumerate(rows):
                cells: List[Any] = list(row)
                # The song cell (column 1 of the pure rows) becomes a terminal
                # hyperlink when the row has a Spotify identity — visible text
                # unchanged, so it still identifies the track without OSC 8.
                url = self.row_urls[index] if index < len(self.row_urls) else ""
                if url and len(cells) > 1:
                    cells[1] = link_text(cells[1], url)
                table.add_row(" ", *cells, key=str(index))
        self.query_one("#results_top", Static).update(self._render_top())
        self.query_one("#results_footer", Static).update(self._render_footer())
        if self.results:
            self._show_detail(0)
            table.focus()
        else:
            self._set_status(
                Text("no cached results — run /search or /find first", style="dim"),
            )

    # ------------------------------------------------------------------
    # Chrome
    # ------------------------------------------------------------------

    def _render_top(self) -> Text:
        line = Text()
        line.append("tunr results", style=f"bold {ACCENT_WHITE}")
        line.append("  ")
        query = str(getattr(self.cli, "last_search_query", None) or "")
        if query:
            line.append(_clip(query, 60), style=ACCENT_BLUE)
            line.append("  ")
        ranked = bool(getattr(self.cli, "last_find_ranked", None))
        order = "taste-ranked (/find)" if ranked else "relevance (/search)"
        line.append(f"{len(self.results)} rows · {order}", style="dim")
        return line

    def _render_footer(self) -> Text:
        footer = Text(
            "enter find-similar · i inspect · space select · c copy id"
            " · o url · a accept · p playlist · esc close",
            style="dim",
        )
        if self.selected:
            footer.append(" · ", style="dim")
            footer.append(f"{len(self.selected)} selected", style=ACCENT_GREEN)
        return footer

    def _set_status(self, message: Text) -> None:
        self.query_one("#results_detail", Static).update(message)

    # ------------------------------------------------------------------
    # Cursor + inspection (app-thread db reads; no workers here)
    # ------------------------------------------------------------------

    def _cursor_index(self) -> Optional[int]:
        if not self.results:
            return None
        table = self.query_one(DataTable)
        index = table.cursor_row
        if 0 <= index < len(self.results):
            return index
        return None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a row → close, handing the app an editable /find seeded
        with the row. The app inserts it into the command input and the user
        finishes/submits — never auto-submitted (palette convention)."""
        index = self._cursor_index()
        if index is None:
            return
        command = prefill_for_item(self.results[index])
        if not command:
            self._set_status(Text("nothing usable on this row to search from", style="yellow"))
            return
        self.dismiss(ResultsAction(mode="prefill", prefill=command))

    def action_inspect_row(self) -> None:
        """i → inspect the cursor row via cli.debug_track's payload."""
        index = self._cursor_index()
        if index is not None:
            self._show_detail(index, inspect=True)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        index = self._cursor_index()
        if index is not None:
            self._show_detail(index)

    def _show_detail(self, index: int, inspect: bool = False) -> None:
        item = self.results[index]
        readout = Text()
        song = str(item.get("song") or item.get("name") or "")
        artist = str(item.get("artist") or "")
        url = self.row_urls[index] if index < len(self.row_urls) else ""
        # link_text with a falsy url is a plain styled Text — headline text is
        # identical either way; a known Spotify identity just makes it a link.
        readout.append_text(
            link_text(_clip(f"{song} — {artist}", 70), url, style=f"bold {ACCENT_WHITE}")
        )
        readout.append("  ")
        readout.append(_fmt_score(item), style=f"bold {ACCENT_ORANGE}")
        providers = item.get("providers") or []
        if providers:
            readout.append(" · ", style="dim")
            readout.append(", ".join(str(p) for p in providers), style="dim")
        if inspect:
            readout.append("\n")
            readout.append_text(self._inspect_line(track_id_of(item)))
        readout.append("\n")
        readout.append(
            f"track {track_id_of(item) or 'unknown'}",
            style=CAPTION_STYLE,
        )
        self._set_status(readout)

    def _inspect_line(self, track_id: str) -> Text:
        """One dense line from cli.debug_track (synchronous app-thread read)."""
        payload: Optional[Mapping[str, Any]] = None
        if track_id:
            try:
                payload = self.cli.debug_track(track_id)
            except Exception:
                logger.debug("debug_track failed for %s", track_id, exc_info=True)
        if not payload:
            return Text("not in the local db yet — accept it to store it", style="dim")
        track = payload.get("track") or {}
        context = payload.get("context") or {}
        line = Text()
        line.append(f"status {track.get('status') or 'cached'}", style=ACCENT_GREEN)
        line.append(" · ", style="dim")
        line.append(
            f"spotify {'yes' if track.get('spotify_id') else 'no'}",
            style="dim",
        )
        line.append(" · ", style="dim")
        ratio = context.get("strict_ratio") if isinstance(context, Mapping) else None
        strict = f"{float(ratio):.2f}" if isinstance(ratio, (int, float)) else "—"
        line.append(f"strict {strict}", style="dim")
        line.append(" · ", style="dim")
        line.append(f"{len(payload.get('sources') or [])} sources", style="dim")
        line.append(" · ", style="dim")
        line.append(f"{len(payload.get('listens') or [])} listens", style="dim")
        return line

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def action_toggle_select(self) -> None:
        index = self._cursor_index()
        if index is None:
            return
        if index in self.selected:
            self.selected.discard(index)
            marker = " "
        else:
            self.selected.add(index)
            marker = SELECT_MARK
        table = self.query_one(DataTable)
        table.update_cell_at(Coordinate(index, 0), Text(marker, style=ACCENT_GREEN))
        self.query_one("#results_footer", Static).update(self._render_footer())

    def _effective_ids(self) -> List[str]:
        """The track ids an action applies to: the selected subset, or the
        cursor row when nothing is selected (single-row acceptance)."""
        indexes = sorted(self.selected)
        if not indexes:
            cursor = self._cursor_index()
            indexes = [cursor] if cursor is not None else []
        ids = [track_id_of(self.results[i]) for i in indexes]
        return [track_id for track_id in ids if track_id]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_spotify_url(self) -> None:
        """o → print the row's Spotify link (print only — never the browser,
        never the network: the cached row first, then the local db record)."""
        index = self._cursor_index()
        if index is None:
            return
        item = self.results[index]
        # Same resolution the table/detail used (cached row, then local db) —
        # precomputed once in __init__ via resolve_spotify_url.
        url = self.row_urls[index] if index < len(self.row_urls) else ""
        if not url:
            self._set_status(
                Text(
                    "no spotify link cached for this row — accept it to a playlist to resolve one",
                    style="yellow",
                )
            )
            return
        song = str(item.get("song") or item.get("name") or "").strip()
        artist = str(item.get("artist") or "").strip()
        label = " — ".join(part for part in (song, artist) if part) or track_id_of(item)
        self._set_status(Text(url, style=ACCENT_BLUE))
        self._log_to_app(Text(f"{label}: {url}", style="dim"))

    def action_copy_track_id(self) -> None:
        """c → copy the cursor row's track id to the clipboard (OSC-52)."""
        index = self._cursor_index()
        if index is None:
            return
        track_id = track_id_of(self.results[index])
        if not track_id:
            self._set_status(Text("no track id for this row", style="yellow"))
            return
        try:
            self.app.copy_to_clipboard(track_id)
        except Exception:
            # Clipboard support is terminal-dependent; the scrollback line
            # below still shows the id in selectable text form.
            logger.debug("copy_to_clipboard failed for %s", track_id, exc_info=True)
        self._set_status(Text(f"copied {track_id}", style="dim"))
        self._log_to_app(Text(f"Copied track id to clipboard: {track_id}", style="dim"))

    def _log_to_app(self, message: Text) -> None:
        """Append a confirmation line to the main app's scrollback.

        Duck-typed so the screen keeps working under any host app (tests push
        it onto a bare Textual App with no append_log): missing seam → skip.
        """
        append = getattr(self.app, "append_log", None)
        if not callable(append):
            return
        try:
            append(message)
        except Exception:
            logger.debug("Could not write to the app scrollback", exc_info=True)

    def action_accept_selected(self) -> None:
        ids = self._effective_ids()
        if not ids:
            self._set_status(Text("nothing to accept — select rows with space", style="yellow"))
            return
        self.dismiss(ResultsAction(mode="db", track_ids=ids))

    def action_playlist_selected(self) -> None:
        ids = self._effective_ids()
        if not ids:
            self._set_status(Text("nothing to add — select rows with space", style="yellow"))
            return
        prompt = self.query_one("#results_prompt", Input)
        prompt.display = True
        prompt.value = ""
        prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        name = event.value.strip()
        if not name:
            self._set_status(Text("enter a playlist name (esc cancels)", style="yellow"))
            return
        ids = self._effective_ids()
        if not ids:
            self._hide_prompt()
            self._set_status(Text("nothing to add — select rows with space", style="yellow"))
            return
        self.dismiss(ResultsAction(mode="playlist", track_ids=ids, playlist_name=name))

    def _hide_prompt(self) -> None:
        prompt = self.query_one("#results_prompt", Input)
        prompt.display = False
        if self.results:
            self.query_one(DataTable).focus()

    def action_close(self) -> None:
        prompt = self.query_one("#results_prompt", Input)
        if prompt.display:
            # Esc while naming a playlist cancels the prompt, not the screen.
            self._hide_prompt()
            return
        self.dismiss(None)
