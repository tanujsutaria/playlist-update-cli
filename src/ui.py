from __future__ import annotations

import json
import math
from typing import Any, Callable, List, Optional, Sequence, Tuple, Union

from rich import box
from rich.align import Align
from rich.cells import cell_len
from rich.color import Color, blend_rgb
from rich.color_triplet import ColorTriplet
from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.measure import Measurement
from rich.panel import Panel
from rich.rule import Rule
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console()
# Dedicated stderr console so `error()` can surface failures in --json mode
# without polluting the machine-readable stdout stream.
_stderr_console = Console(stderr=True)
_output_sink: Optional[Callable[[RenderableType], None]] = None
_preview_sink: Optional[Callable[[Optional[RenderableType]], None]] = None
_json_mode: bool = False

# --- ink: the tunr visual language (OP-1 edition) -----------------------------
# Codified style grammar — every chart in the app speaks the same dialect:
#   * fill bars (hbar / stacked_bar) = counted facts ("we counted tracks")
#   * lollipops                      = measured probabilities (a classifier call)
#   * MISSING_STYLE grey             = data we DO NOT have — always drawn, never hidden
#   * RAMP_LO -> RAMP_HI gradients   = intensity over an ORDERED axis only
#     (sparklines, growth curves, heat strips) — never on ranked bars.
# `green` stays "succeeded", `yellow` stays "warning", and `red` stays reserved
# exclusively for failure (the `error()` contract) — never decorative.
#
# OP-1 palette mapping (Teenage Engineering): blue is the data ink (counted
# facts, titles, accents), orange owns markers/selection (and the table-header
# accent — the roles magenta used to play), warm white is the neutral tick,
# and missing data stays grey — the honesty doctrine survives the re-skin.
ACCENT_BLUE = "#00b4e6"  # op-1 blue — primary data ink
ACCENT_GREEN = "#00e05a"  # op-1 green — success only, never decorative
ACCENT_WHITE = "#fffff6"  # op-1 warm white — neutral foreground ink
ACCENT_ORANGE = "#f26200"  # op-1 orange — markers / selection only

RAMP_LO = ColorTriplet(28, 36, 54)  # dark slate — cold end of every gradient
RAMP_HI = ColorTriplet(0, 180, 230)  # op-1 blue — hot end; matches ACCENT_BLUE
BAR_STYLE = ACCENT_BLUE  # primary data fill (counted facts)
FILL_STYLE = "#0077a8"  # coverage fills (have-vs-missing bars) — darker blue
TRACK_STYLE = "grey30"  # the unfilled remainder of a bar (always drawn)
MISSING_STYLE = "grey23"  # data we DO NOT have — always shown, never hidden
CARD_BORDER = "#8a8d8f"  # rounded panel borders — op-1 muted grey (quiet, not white)
MARKER_STYLE = ACCENT_ORANGE  # measurement markers (lollipop dots)
NEUTRAL_TICK_STYLE = f"bold {ACCENT_WHITE}"  # the ┊ classifier-neutral / target tick
VALUE_STYLE = "bold"  # numeric values
CAPTION_STYLE = "dim italic"  # honesty captions under every chart
TABLE_HEADER_STYLE = f"bold {ACCENT_ORANGE}"  # table headers (was bold magenta)
SUBSECTION_STYLE = f"bold {ACCENT_BLUE}"  # subsection lines & panel titles (was bold cyan)

# A style argument: either a Rich style string or a Style object. The RichLog
# sink runs with markup=False, so styles must be real objects/strings on Text
# segments — never markup strings embedded in content.
StyleLike = Union[str, Style]


def set_json_mode(enabled: bool) -> None:
    """Toggle machine-readable JSON mode.

    While on, every decorative helper (section/table/info/charts/…) is silenced
    at the single `_emit` choke point, so a command emits nothing but the one
    `emit_json` payload — keeping stdout clean for piping.
    """
    global _json_mode
    _json_mode = enabled


def is_json_mode() -> bool:
    return _json_mode


def emit_json(payload: object) -> None:
    """Print a JSON payload to stdout, bypassing Rich entirely.

    Uses the builtin `print` (not the Rich console) so the output is never
    wrapped, styled, or routed to a TUI sink — it stays valid JSON for `| jq`.
    """
    print(json.dumps(payload, indent=2, default=str))


def set_output_sink(sink: Optional[Callable[[RenderableType], None]]) -> None:
    """Route UI renderables to an alternate sink (e.g., a Textual RichLog)."""
    global _output_sink
    _output_sink = sink


def set_preview_sink(sink: Optional[Callable[[Optional[RenderableType]], None]]) -> None:
    """Route preview renderables to a dedicated sink (optional)."""
    global _preview_sink
    _preview_sink = sink


def _emit(renderable: Union[RenderableType, str]) -> None:
    if _json_mode:
        return
    if isinstance(renderable, str):
        renderable = Text(renderable)
    if _output_sink:
        _output_sink(renderable)
    else:
        console.print(renderable)


def _emit_preview(renderable: Optional[RenderableType]) -> None:
    if _json_mode:
        return
    if _preview_sink:
        _preview_sink(renderable)


def section(title: str, subtitle: Optional[str] = None) -> None:
    header = Text(title, style="bold")
    if subtitle:
        header.append(f" • {subtitle}", style="dim")
    _emit(Rule(header))


def subsection(title: str) -> None:
    _emit(Text(title, style=SUBSECTION_STYLE))


def table(headers: list[Any], rows: list[list[Any]]) -> None:
    t = Table(show_header=True, header_style=TABLE_HEADER_STYLE, box=box.SIMPLE, expand=True)
    for header in headers:
        t.add_column(str(header), overflow="fold", no_wrap=False)
    for row in rows:
        # Rich Text cells pass through untouched so callers can style cells
        # (e.g. green when a constraint is satisfied); everything else is
        # coerced with str() as before.
        t.add_row(*[cell if isinstance(cell, Text) else str(cell) for cell in row])
    _emit(t)


def preview_table(headers: list[Any], rows: list[list[Any]], title: Optional[str] = None) -> None:
    if not _preview_sink:
        return
    t = Table(show_header=True, header_style=TABLE_HEADER_STYLE, box=box.SIMPLE, expand=True)
    for header in headers:
        t.add_column(str(header), overflow="fold", no_wrap=False)
    for row in rows:
        t.add_row(*[cell if isinstance(cell, Text) else str(cell) for cell in row])
    if title:
        _emit_preview(Panel(t, title=title, border_style=ACCENT_BLUE))
    else:
        _emit_preview(t)


def clear_preview() -> None:
    _emit_preview(None)


def key_value_table(rows: list[list[Any]]) -> None:
    t = Table(show_header=False, box=box.SIMPLE, expand=True)
    t.add_column("Key", style="bold", overflow="fold", no_wrap=False)
    t.add_column("Value", overflow="fold", no_wrap=False)
    for key, value in rows:
        t.add_row(str(key), str(value))
    _emit(t)


def info(message: str) -> None:
    _emit(Text(message, style="green"))


def warning(message: str) -> None:
    _emit(Text(message, style="yellow"))


def error(message: str, *, title: str = "Error") -> None:
    """Render a failure as a red panel. Red == the command failed.

    Unlike every other helper this is NOT silenced by json mode — a failure
    must always reach the user. With an output sink installed it routes there
    (the TUI scrollback); otherwise in json mode it prints to stderr so the
    `--json` stdout payload stays pure; otherwise it prints to the console.
    """
    renderable = Panel(Text(message, style="red"), title=title, border_style="red")
    if _output_sink:
        _output_sink(renderable)
    elif is_json_mode():
        _stderr_console.print(renderable)
    else:
        console.print(renderable)


def notice(message: str) -> None:
    """A dim, low-emphasis line for neutral "nothing to do" outcomes.

    Use this instead of `info` (green == succeeded) when a command completed
    but produced nothing: no results, empty library, nothing to rank.
    """
    _emit(Text(message, style="dim"))


def summary_panel(text: str, title: str = "Summary") -> None:
    """Render a synthesized prose summary in a bordered panel.

    Routes through `_emit`, so it honours json-mode (silenced) and the Textual
    output-sink (RichLog) exactly like every other helper. A falsy/blank text is
    a no-op so callers can pass an optional summary unguarded.
    """
    if not text:
        return
    _emit(Panel(Text(text), title=title, border_style="green"))


def json_output(payload: object) -> None:
    rendered = Syntax(json.dumps(payload, indent=2), "json", theme="ansi_dark", word_wrap=True)
    _emit(rendered)


# ---------------------------------------------------------------------------
# Lightweight, dependency-free charts (Unicode block glyphs).
# These render through `_emit` like every other helper, so they work
# identically in the console and inside the Textual RichLog sink.
# ---------------------------------------------------------------------------

_SPARK_TICKS = "▁▂▃▄▅▆▇█"
# Eighth-width left blocks, indexed by eighths of a cell: idx 0 == empty.
_BAR_EIGHTHS = " ▏▎▍▌▋▊▉"
_BAR_FULL = "█"


def sparkline(values: Sequence[float]) -> str:
    """Render a numeric series as a compact one-line Unicode sparkline.

    Values are min-max scaled across the series. An empty series yields an
    empty string; a flat series yields a row of mid-height ticks (so "no
    variation" reads differently from "no data").
    """
    nums = [float(v) for v in values]
    if not nums:
        return ""
    low = min(nums)
    high = max(nums)
    span = high - low
    if span == 0:
        return _SPARK_TICKS[len(_SPARK_TICKS) // 2] * len(nums)
    last = len(_SPARK_TICKS) - 1
    return "".join(_SPARK_TICKS[int((v - low) / span * last + 0.5)] for v in nums)


def _bar(fraction: float, width: int) -> str:
    """A horizontal bar `width` cells wide filled to `fraction` (0..1),
    using eighth-of-a-cell partials for sub-cell precision."""
    fraction = max(0.0, min(1.0, fraction))
    eighths = int(round(fraction * width * 8))
    full, remainder = divmod(eighths, 8)
    bar = _BAR_FULL * full
    if remainder:
        bar += _BAR_EIGHTHS[remainder]
    return bar


def bar_chart(
    labels: Sequence[Any],
    values: Sequence[float],
    *,
    width: int = 28,
    value_fmt: Optional[Callable[[float], str]] = None,
) -> None:
    """Emit a horizontal bar chart as a 3-column table: label | bar | value.

    Bars are scaled to the largest value in the series. `value_fmt` overrides
    how the numeric column is formatted (defaults to int-or-2dp).
    """
    labels = [str(label) for label in labels]
    nums = [float(v) for v in values]
    if not nums:
        info("No data to chart.")
        return
    peak = max(nums)
    scale = peak if peak > 0 else 1.0

    def _fmt(v: float) -> str:
        if value_fmt is not None:
            return value_fmt(v)
        return str(int(v)) if v == int(v) else f"{v:.2f}"

    chart = Table(show_header=False, box=box.SIMPLE, expand=False, pad_edge=False)
    chart.add_column("Label", style=ACCENT_BLUE, overflow="fold", no_wrap=True)
    chart.add_column("Bar", no_wrap=True)
    chart.add_column("Value", justify="right", no_wrap=True)
    for label, value in zip(labels, nums):
        chart.add_row(label, hbar(value / scale, width), Text(_fmt(value), style=VALUE_STYLE))
    _emit(chart)


# ---------------------------------------------------------------------------
# ink primitives — the shared visual toolkit for /taste, /stats, /profile.
# Builders return renderables and never emit; emitters route through `_emit`
# so json-mode silencing and TUI sink routing come for free. Styles are
# always Style objects or style strings — never markup strings.
# ---------------------------------------------------------------------------


def _clamp01(value: float) -> float:
    """Clamp to [0, 1]; NaN (and any non-finite garbage) reads as 0.0."""
    value = float(value)
    if math.isnan(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _finite_nonneg(value: float) -> float:
    """Clamp to >= 0; NaN/inf read as 0.0 (stacked segments must stay finite)."""
    value = float(value)
    if not math.isfinite(value):
        return 0.0
    return max(0.0, value)


def _cells_text(cells: Sequence[Tuple[str, StyleLike]]) -> Text:
    """Assemble per-cell (char, style) pairs into a Text, merging consecutive
    cells that share a style into a single span."""
    text = Text()
    run: List[str] = []
    run_style: Optional[StyleLike] = None
    for char, style in cells:
        if run and style == run_style:
            run.append(char)
        else:
            if run:
                text.append("".join(run), style=run_style)
            run = [char]
            run_style = style
    if run:
        text.append("".join(run), style=run_style)
    return text


def _bg_style(style: StyleLike) -> Style:
    """Coerce a style into a background style for painting space cells."""
    if isinstance(style, Style):
        if style.bgcolor is not None:
            return style
        if style.color is not None:
            return Style(bgcolor=style.color)
        return style
    return Style(bgcolor=style)


def hbar(
    fraction: float,
    width: int = 24,
    *,
    style: StyleLike = BAR_STYLE,
    track_style: StyleLike = TRACK_STYLE,
    track_char: str = "╌",
    tick: Optional[float] = None,
    tick_style: StyleLike = NEUTRAL_TICK_STYLE,
) -> Text:
    """A tracked fill bar for COUNTED FACTS: eighth-block fill over a dim track.

    The unfilled remainder is always drawn (`track_char`), so a small value
    still reads against its full scale. An optional `tick` (0..1) overlays a
    target/neutral marker `┊` (drawn over fill or track). `fraction` and
    `tick` clamp to [0, 1]; NaN reads as 0.0; `width < 1` yields an empty Text.
    """
    if width < 1:
        return Text("")
    fill = _bar(_clamp01(fraction), width)
    cells: List[Tuple[str, StyleLike]] = [(char, style) for char in fill]
    cells.extend((track_char, track_style) for _ in range(width - len(fill)))
    if tick is not None:
        cells[int(round(_clamp01(tick) * (width - 1)))] = ("┊", tick_style)
    return _cells_text(cells)


def lollipop(
    fraction: float,
    width: int = 28,
    *,
    marker: str = "●",
    marker_style: StyleLike = MARKER_STYLE,
    track_style: StyleLike = "grey35",
    tick: Optional[float] = None,
    tick_style: StyleLike = NEUTRAL_TICK_STYLE,
) -> Text:
    """A measurement marker on a dim track, for MEASURED PROBABILITIES.

    Lollipops-not-bars is the honesty grammar: a dot on a track reads as "a
    classifier measured this", not "we counted this many". An optional `tick`
    draws a `┊` (e.g. 0.5 = classifier-neutral); the marker wins on collision.
    Clamping as `hbar`; `width < 2` collapses to just the marker.
    """
    fraction = _clamp01(fraction)
    if width < 2:
        return Text(marker, style=marker_style)
    cells: List[Tuple[str, StyleLike]] = [("─", track_style) for _ in range(width)]
    if tick is not None:
        cells[int(round(_clamp01(tick) * (width - 1)))] = ("┊", tick_style)
    cells[min(width - 1, int(round(fraction * (width - 1))))] = (marker, marker_style)
    return _cells_text(cells)


def stacked_bar(parts: Sequence[Tuple[float, StyleLike]], width: int = 36) -> Text:
    """A percent-stacked bar of background-colored spaces, always full width.

    Fractions clamp to >= 0. If they sum to less than 1, an automatic
    `MISSING_STYLE` remainder segment is appended — missing data is always
    drawn, never hidden. If they sum to more than 1, all parts are scaled
    down proportionally. The final segment absorbs rounding so the result is
    exactly `width` cells. Empty `parts` render a full-width missing bar (an
    honest "nothing here"); `width < 1` yields an empty Text.
    """
    if width < 1:
        return Text("")
    segments: List[Tuple[float, StyleLike]] = [
        (_finite_nonneg(fraction), style) for fraction, style in parts
    ]
    total = sum(fraction for fraction, _ in segments)
    if total > 1.0:
        segments = [(fraction / total, style) for fraction, style in segments]
    elif total < 1.0:
        segments.append((1.0 - total, MISSING_STYLE))
    text = Text()
    cursor = 0.0
    allocated = 0
    for index, (fraction, style) in enumerate(segments):
        if index == len(segments) - 1:
            cells = width - allocated  # the final segment absorbs rounding
        else:
            cursor += fraction
            upto = min(width, max(allocated, int(round(cursor * width))))
            cells = upto - allocated
        allocated += cells
        if cells > 0:
            text.append(" " * cells, style=_bg_style(style))
    return text


def sparkline_text(
    values: Sequence[float],
    *,
    low: ColorTriplet = RAMP_LO,
    high: ColorTriplet = RAMP_HI,
) -> Text:
    """Color-ramped twin of `sparkline()`: identical min-max tick math, with
    each tick colored along the `low` -> `high` ramp (a gradient is allowed
    here because a sparkline is an ordered axis). Empty input yields an empty
    Text; a flat series (and a single value) renders mid-height ticks at the
    ramp midpoint — "no variation" reads differently from "no data".
    """
    nums = [float(v) for v in values]
    nums = [v if math.isfinite(v) else 0.0 for v in nums]
    if not nums:
        return Text("")
    low_val = min(nums)
    span = max(nums) - low_val
    last = len(_SPARK_TICKS) - 1
    text = Text()
    for v in nums:
        fraction = 0.5 if span == 0 else (v - low_val) / span
        tick_char = (
            _SPARK_TICKS[len(_SPARK_TICKS) // 2]
            if span == 0
            else _SPARK_TICKS[int(fraction * last + 0.5)]
        )
        color = Color.from_triplet(blend_rgb(low, high, cross_fade=fraction))
        text.append(tick_char, style=Style(color=color))
    return text


def heat_strip(
    values: Sequence[float],
    *,
    cell_width: int = 2,
    low: ColorTriplet = RAMP_LO,
    high: ColorTriplet = RAMP_HI,
) -> Text:
    """A one-line heatmap: one background-ramped cell per value.

    Values are ABSOLUTE 0..1 — never min-max rescaled: a strip of zeros
    renders uniformly cold, which is exactly the honest rendering of "no
    overlap anywhere". Each value clamps to [0, 1]; empty input (or a
    `cell_width < 1`) yields an empty Text.
    """
    if cell_width < 1:
        return Text("")
    text = Text()
    for v in values:
        color = Color.from_triplet(blend_rgb(low, high, cross_fade=_clamp01(v)))
        text.append(" " * cell_width, style=Style(bgcolor=color))
    return text


def chips(
    values: Sequence[str],
    *,
    max_items: int = 3,
    style: StyleLike = ACCENT_BLUE,
    sep: str = " · ",
) -> Text:
    """Inline value chips: `dream pop · shoegaze`, separators dim.

    Values beyond `max_items` truncate to a dim ` +N`. Empty input renders a
    dim `—` so a table cell reads as an honest blank, never an empty hole.
    """
    if not values:
        return Text("—", style="dim")
    shown = list(values)[:max_items]
    text = Text()
    for index, value in enumerate(shown):
        if index:
            text.append(sep, style="dim")
        text.append(value, style=style)
    extra = len(values) - len(shown)
    if extra > 0:
        text.append(f" +{extra}", style="dim")
    return text


_CHROME_PAD = 6  # border + "─ " / " ─" decoration around border title/subtitle


def _border_caption(text: str, width: int) -> Text:
    """A subtitle Text guaranteed to fit a `width`-cell panel border.

    Rich hard-crops border text that outgrows the panel — no ellipsis, so the
    honesty-caption slot would silently lose its tail. Pre-truncating with a
    visible `…` makes any unavoidable cut intentional, never silent.
    """
    caption_text = Text(text, style=CAPTION_STYLE)
    if cell_len(text) > width - _CHROME_PAD:
        caption_text.truncate(max(1, width - _CHROME_PAD), overflow="ellipsis")
    return caption_text


def _fit_chrome_width(
    panel: Panel, title: Optional[str], caption: Optional[str], *, demote: bool = False
) -> Optional[str]:
    """Fit a panel's border chrome inside the panel AND the console, honestly.

    Rich hard-crops border text that outgrows a panel — no ellipsis — so an
    overlong subtitle (the honesty-caption slot) would silently lose its tail.
    Rules, in priority order:

    * a caller-fixed `panel.width` is the caller's responsibility (per
      stat_cards) — except that an overlong subtitle is pre-truncated to that
      width with a visible `…`, so any cut is intentional, never silent;
    * the TITLE may floor a width-less panel's width (titles are short,
      load-bearing literals), clamped to the console width and `…`-truncated
      when even the console cannot hold it;
    * the CAPTION never widens the panel — a footnote must not dictate layout,
      nor outgrow the console where Rich would crop it. When it fits it rides
      the border; otherwise, with `demote=True` it is removed and RETURNED so
      the emitting caller renders it as a wrapped `caption()` line below the
      panel (wrapping can never cut the honesty text). With `demote=False`
      (composed, non-emitting panels) it stays attached: the panel widens up
      to the console and the subtitle is `…`-truncated only if that still
      cannot hold it.

    Returns the demoted caption, or None when the border kept it.
    """
    avail = console.options.max_width
    if panel.width is not None:
        if caption and cell_len(caption) + _CHROME_PAD > panel.width:
            panel.subtitle = _border_caption(caption, panel.width)
        return None
    width = Measurement.get(console, console.options, panel.renderable).maximum + 4
    if title:
        needed = cell_len(title) + _CHROME_PAD
        if needed > avail and isinstance(panel.title, Text):
            panel.title.truncate(max(1, avail - _CHROME_PAD), overflow="ellipsis")
            needed = avail
        if needed > width:
            panel.width = needed
            width = needed
    if not caption or cell_len(caption) + _CHROME_PAD <= min(width, avail):
        return None
    if demote:
        panel.subtitle = None
        return caption
    needed = cell_len(caption) + _CHROME_PAD
    panel.width = min(needed, avail)
    if needed > avail:
        panel.subtitle = _border_caption(caption, avail)
    return None


def stat_cards(
    cards: Sequence[Tuple[str, Union[str, Text], Optional[str]]],
    *,
    width: int = 22,
    border_style: StyleLike = CARD_BORDER,
) -> None:
    """A dashboard masthead row of stat cards, one `Columns` emission.

    Each `(title, value, sub)` becomes a rounded panel: dim title on the
    border, bold centered value, optional dim sub-line. `Columns` auto-rewraps
    at narrow widths, so degradation is free. A border title must fit in
    `width - 6` cells or Rich would truncate it mid-glyph — longer titles are
    defensively truncated here with `…`; callers must size widths so pinned
    literals never truncate. Empty `cards` is a no-op.
    """
    if not cards:
        return
    max_title = max(1, width - 6)
    panels: List[Panel] = []
    for title, value, sub in cards:
        title_text = Text(title, style="dim")
        if cell_len(title) > max_title:
            title_text.truncate(max_title, overflow="ellipsis")
        value_text = value if isinstance(value, Text) else Text(str(value), style=VALUE_STYLE)
        lines: List[RenderableType] = [value_text]
        if sub:
            lines.append(Text(sub, style="dim"))
        # Each line centers independently: one Align around the whole Group
        # would center the block but left-align the shorter line inside it,
        # making adjacent cards visibly disagree.
        panels.append(
            Panel(
                Group(*[Align.center(line) for line in lines]),
                title=title_text,
                box=box.ROUNDED,
                border_style=border_style,
                width=width,
            )
        )
    _emit(Columns(panels, padding=(0, 1)))


def chart_panel(
    title: str,
    rows: Sequence[Tuple[str, float]],
    *,
    kind: str = "bar",  # "bar" | "lollipop"
    width: int = 24,
    label_width: Optional[int] = None,
    max_value: Optional[float] = None,  # None -> peak-scaled (ranking look)
    value_fmt: Optional[Callable[[float], str]] = None,
    tick: Optional[float] = None,  # forwarded to hbar/lollipop
    caption: Optional[str] = None,  # dim-italic Panel subtitle (honesty slot)
    border_style: StyleLike = CARD_BORDER,
    bar_style: StyleLike = BAR_STYLE,
    footer_rows: Optional[Sequence[Tuple[str, Union[str, Text], Union[str, Text]]]] = None,
    emit: bool = True,
) -> Panel:
    """A rounded chart panel: label | bar/lollipop | value rows, in given order.

    Scale is `max_value` when given (absolute — e.g. probabilities with
    `max_value=1.0`), else the series peak (a ranking look, not percentages —
    captions must say which). `kind="lollipop"` draws measurement markers
    (with `tick`) instead of fills. `caption` lands as the dim-italic panel
    subtitle — the standing honesty-footnote slot; a caption that outgrows
    the panel (or the console) never widens or crops, it is emitted as a
    wrapped `caption()` line below the panel instead. `footer_rows` appends
    unscaled `(label, middle, value)` rows below the chart after a blank
    spacer row (e.g. an annotated tempo axis); they only apply when `rows`
    is non-empty. Returns the Panel; `emit=True` also emits it. Empty rows
    render one dim "no data" line; all-zero values render empty tracks with
    their `0`s still visible.
    """
    body: RenderableType
    if not rows:
        body = Text("no data", style="dim")
    else:
        values = [_finite_nonneg(value) for _, value in rows]
        scale = float(max_value) if max_value is not None else max(values)
        grid = Table.grid(padding=(0, 1))
        grid.add_column(
            justify="right", style="dim", no_wrap=True, width=label_width, overflow="ellipsis"
        )
        grid.add_column(no_wrap=True)
        grid.add_column(justify="right", style=VALUE_STYLE, no_wrap=True)
        for (label, raw), value in zip(rows, values):
            fraction = value / scale if scale > 0 else 0.0
            bar: Text
            if kind == "lollipop":
                bar = lollipop(fraction, width, tick=tick)
            else:
                bar = hbar(fraction, width, style=bar_style, tick=tick)
            grid.add_row(label, bar, value_fmt(raw) if value_fmt is not None else str(raw))
        if footer_rows:
            grid.add_row("", "", "")
            for foot_label, middle, foot_value in footer_rows:
                grid.add_row(foot_label, middle, foot_value)
        body = grid
    panel = Panel(
        body,
        title=Text(title, style=SUBSECTION_STYLE),
        title_align="left",
        subtitle=Text(caption, style=CAPTION_STYLE) if caption else None,
        subtitle_align="right",
        box=box.ROUNDED,
        border_style=border_style,
    )
    demoted = _fit_chrome_width(panel, title, caption, demote=emit)
    if emit:
        _emit(panel)
        if demoted:
            _emit(Text(demoted, style=CAPTION_STYLE))
    return panel


def facet_columns(
    blocks: Sequence[Tuple[str, Sequence[Tuple[str, float]], Optional[str]]],
    *,
    bar_width: int = 10,
    panel_width: int = 44,
) -> None:
    """Side-by-side mini chart panels via one `Columns` emission.

    `blocks = (title, rows, caption)`. Stacks vertically at narrow widths
    (free degradation). A block with empty rows shows the dim "no data" line;
    a single block emits alone; empty `blocks` is a no-op. Captions attach
    after the width is fixed (pre-truncated with a visible `…` if ever too
    long) — a composed panel cannot demote its caption to a line below.
    """
    if not blocks:
        return
    panels: List[Panel] = []
    for title, rows, block_caption in blocks:
        panel = chart_panel(title, rows, width=bar_width, emit=False)
        panel.width = panel_width
        if block_caption:
            panel.subtitle = _border_caption(block_caption, panel_width)
        panels.append(panel)
    if len(panels) == 1:
        _emit(panels[0])
    else:
        _emit(Columns(panels, padding=(0, 1)))


def coverage_panel(
    title: Optional[str],
    rows: Sequence[Tuple[str, int, int]],
    *,
    width: int = 36,
    caption: Optional[str] = None,
) -> None:
    """The coverage ledger: each `(label, have, total)` renders a stacked bar
    whose grey remainder IS the missing data, plus `have/total` and `(pct%)`.

    A row with `total == 0` draws a full-grey bar, `0/0`, and a dim `—` (no
    division). When ALL rows have `total == 0` (or there are no rows), a
    `notice` replaces the panel. `have` clamps defensively into [0, total].
    `title=None` renders an untitled panel (for use under a `section` Rule).
    `width` is the bar's ceiling: at narrow consoles only the (decorative)
    bar shrinks — labels and counts carry the numbers, so they survive first,
    the same priority the rest of the system uses. A caption that outgrows
    the panel is emitted as a wrapped `caption()` line below it, never cut.
    """
    if all(total <= 0 for _, _, total in rows):
        notice("No data to chart.")
        return
    counts = [
        f"{max(0, min(have, total))}/{total}" if total > 0 else "0/0" for _, have, total in rows
    ]
    # Cells the text columns need: labels + counts + "(100%)" + per-column
    # grid padding (4 columns x 2) + panel border/padding (4).
    overhead = (
        max(cell_len(str(label)) for label, _, _ in rows)
        + max(cell_len(count) for count in counts)
        + 6
        + 12
    )
    width = max(8, min(width, console.options.max_width - overhead))
    grid = Table.grid(padding=(0, 1))
    grid.add_column(justify="right", style="dim", no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column(justify="right", style=VALUE_STYLE, no_wrap=True)
    grid.add_column(style="dim", no_wrap=True)
    for (label, have, total), count in zip(rows, counts):
        if total <= 0:
            grid.add_row(label, stacked_bar([], width), count, "—")
            continue
        fraction = max(0, min(have, total)) / total
        grid.add_row(
            label,
            stacked_bar([(fraction, FILL_STYLE)], width),
            count,
            f"({fraction * 100:.0f}%)",
        )
    panel = Panel(
        grid,
        title=Text(title, style=SUBSECTION_STYLE) if title else None,
        title_align="left",
        subtitle=Text(caption, style=CAPTION_STYLE) if caption else None,
        subtitle_align="right",
        box=box.ROUNDED,
        border_style=CARD_BORDER,
    )
    demoted = _fit_chrome_width(panel, title, caption, demote=True)
    _emit(panel)
    if demoted:
        _emit(Text(demoted, style=CAPTION_STYLE))


def ink_panel(
    body: RenderableType,
    *,
    title: Optional[str] = None,
    caption: Optional[str] = None,
    border_style: StyleLike = CARD_BORDER,
    width: Optional[int] = None,
    emit: bool = True,
) -> Panel:
    """A generic ink-styled rounded panel around an arbitrary renderable.

    Same chrome as `chart_panel` (SUBSECTION_STYLE left title, dim-italic right
    `caption` subtitle as the honesty-footnote slot, ROUNDED box) for blocks
    that aren't label/bar/value charts — e.g. the /taste masthead or the
    core-vs-frontier contrast cards. A caption that outgrows the panel (or
    the console) is emitted as a wrapped `caption()` line below it, never
    cut. Returns the Panel; `emit=True` also emits it (use `emit=False` to
    compose, e.g. into `side_by_side`).
    """
    panel = Panel(
        body,
        title=Text(title, style=SUBSECTION_STYLE) if title else None,
        title_align="left",
        subtitle=Text(caption, style=CAPTION_STYLE) if caption else None,
        subtitle_align="right",
        box=box.ROUNDED,
        border_style=border_style,
        width=width,
    )
    demoted = _fit_chrome_width(panel, title, caption, demote=emit)
    if emit:
        _emit(panel)
        if demoted:
            _emit(Text(demoted, style=CAPTION_STYLE))
    return panel


def side_by_side(*renderables: RenderableType, padding: Tuple[int, int] = (0, 1)) -> None:
    """Emit renderables side by side in one `Columns` (stacks when narrow).

    One renderable emits directly; none is a no-op.
    """
    if not renderables:
        return
    if len(renderables) == 1:
        _emit(renderables[0])
        return
    _emit(Columns(renderables, padding=padding))


def insight(message: str) -> None:
    """A gated, computed finding: `◆ {message}`. Templates only fire on hard
    conditions — every number in an insight is computed, never adjectival."""
    _emit(Text.assemble(("◆ ", ACCENT_BLUE), (message, "")))


def caption(message: str) -> None:
    """The footnote voice: a dim-italic honesty caption under a chart,
    naming the denominator / provenance of what was just drawn."""
    _emit(Text(message, style=CAPTION_STYLE))


def text_line(*parts: Union[str, Text]) -> None:
    """Emit ONE line assembled from pre-styled `Text` parts (plain strings stay
    unstyled). The composed-line primitive for rows that mix styled spans with
    chart fragments — e.g. `dim label · stacked_bar · dim detail` — without
    forcing a table or panel around them. Routes through `_emit`, so json-mode
    silencing and sink routing apply. No parts is a no-op."""
    if not parts:
        return
    line = Text()
    for part in parts:
        if isinstance(part, Text):
            line.append_text(part)
        else:
            line.append(part)
    _emit(line)
