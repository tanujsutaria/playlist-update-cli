from __future__ import annotations

import json
from typing import Any, Callable, Optional, Sequence, Union

from rich import box
from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console()
_output_sink: Optional[Callable[[RenderableType], None]] = None
_preview_sink: Optional[Callable[[Optional[RenderableType]], None]] = None
_json_mode: bool = False


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
    _emit(Text(title, style="bold cyan"))


def table(headers: list[Any], rows: list[list[Any]]) -> None:
    t = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE, expand=True)
    for header in headers:
        t.add_column(str(header), overflow="fold", no_wrap=False)
    for row in rows:
        t.add_row(*[str(cell) for cell in row])
    _emit(t)


def preview_table(headers: list[Any], rows: list[list[Any]], title: Optional[str] = None) -> None:
    if not _preview_sink:
        return
    t = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE, expand=True)
    for header in headers:
        t.add_column(str(header), overflow="fold", no_wrap=False)
    for row in rows:
        t.add_row(*[str(cell) for cell in row])
    if title:
        _emit_preview(Panel(t, title=title, border_style="cyan"))
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
    chart.add_column("Label", style="cyan", overflow="fold", no_wrap=True)
    chart.add_column("Bar", no_wrap=True)
    chart.add_column("Value", justify="right", no_wrap=True)
    for label, value in zip(labels, nums):
        chart.add_row(label, _bar(value / scale, width), _fmt(value))
    _emit(chart)
