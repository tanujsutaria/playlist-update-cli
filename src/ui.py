from __future__ import annotations

import json
from typing import Callable, Optional, Any, Union

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


def set_output_sink(sink: Optional[Callable[[RenderableType], None]]) -> None:
    """Route UI renderables to an alternate sink (e.g., a Textual RichLog)."""
    global _output_sink
    _output_sink = sink


def set_preview_sink(sink: Optional[Callable[[Optional[RenderableType]], None]]) -> None:
    """Route preview renderables to a dedicated sink (optional)."""
    global _preview_sink
    _preview_sink = sink


def _emit(renderable: Union[RenderableType, str]) -> None:
    if isinstance(renderable, str):
        renderable = Text(renderable)
    if _output_sink:
        _output_sink(renderable)
    else:
        console.print(renderable)


def _emit_preview(renderable: Optional[RenderableType]) -> None:
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


def json_output(payload: object) -> None:
    rendered = Syntax(json.dumps(payload, indent=2), "json", theme="ansi_dark", word_wrap=True)
    _emit(rendered)
