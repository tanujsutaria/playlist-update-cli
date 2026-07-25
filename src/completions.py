"""Inline ghost-text autocomplete for the tunr command input.

The Textual ``Input`` widget renders ``suggestion[len(value):]`` as grey ghost
text after the caret and, on right-arrow, replaces the whole value with the
suggestion. Every completion returned here is therefore a FULL-LINE string
that starts with the exact current input value — the pure helper
:func:`complete_line` builds them by appending a tail to ``value`` so the
invariant holds by construction.

Completion precedence (per keystroke):

1. command name — ``/up`` -> ``/update`` (slash-prefixed or bare);
2. current command's flags — ``/update x --fr`` -> ``... --fresh-days``;
3. most recent history line sharing the typed prefix.

``Suggester.get_suggestion`` runs in a Textual worker on every keystroke, so
everything here is pure and in-memory: static command/flag inventories plus
the live history list. No I/O.
"""

from __future__ import annotations

import shlex
from typing import Callable, Mapping, Optional, Sequence

from textual.suggester import Suggester


def complete_line(
    value: str,
    *,
    commands: Sequence[str],
    flags: Mapping[str, Sequence[str]],
    history: Sequence[str],
) -> Optional[str]:
    """Return a full-line completion for ``value``, or None.

    ``commands`` are bare command names (no slash); ``flags`` maps a command
    name to its option strings; ``history`` is oldest-first (the most recent
    line wins the fallback). Unbalanced quotes (shlex ``ValueError`` on
    partial input) simply skip token-aware completion and fall back to
    history.
    """
    if not value.strip():
        return None
    try:
        tokens = shlex.split(value)
    except ValueError:
        # Mid-typing quote imbalance ('/update "My Pl') — history-only.
        tokens = []
    if tokens and not value[-1].isspace():
        if len(tokens) == 1:
            completed = _complete_command(value, tokens[0], commands)
        else:
            completed = _complete_flag(value, tokens, flags)
        if completed is not None:
            return completed
    return _complete_from_history(value, history)


def _complete_command(value: str, token: str, commands: Sequence[str]) -> Optional[str]:
    """Complete the first token against the command inventory."""
    name = token[1:] if token.startswith("/") else token
    if not name or name.startswith("-"):
        return None
    for command in commands:
        if command.startswith(name) and command != name:
            return value + command[len(name) :]
    return None


def _complete_flag(
    value: str, tokens: Sequence[str], flags: Mapping[str, Sequence[str]]
) -> Optional[str]:
    """Complete a trailing ``--fl``-style token against the command's flags."""
    tail = value[max(value.rfind(" "), value.rfind("\t")) + 1 :]
    # Only complete a literal trailing flag: if the raw tail differs from the
    # last shlex token, the "-" is inside quoting ('/find "lo-fi' etc.) and
    # painting a flag tail there would corrupt the line.
    if not tail.startswith("-") or tokens[-1] != tail:
        return None
    first = tokens[0]
    command = first[1:] if first.startswith("/") else first
    for flag in flags.get(command, ()):
        if flag.startswith(tail) and flag != tail:
            return value + flag[len(tail) :]
    return None


def _complete_from_history(value: str, history: Sequence[str]) -> Optional[str]:
    """Most recent history line extending ``value`` (never ``value`` itself)."""
    for line in reversed(history):
        if line.startswith(value) and line != value:
            return line
    return None


class TunrSuggester(Suggester):
    """Ghost-text suggester wired into the command ``Input``.

    ``history`` is a provider callable rather than a list reference: the app
    REBINDS ``self._history`` when it truncates to ``HISTORY_MAX_LINES``, so a
    captured reference would silently go stale.
    """

    def __init__(
        self,
        *,
        commands: Sequence[str],
        flags: Mapping[str, Sequence[str]],
        history: Callable[[], Sequence[str]],
    ) -> None:
        # use_cache=False: history grows during the session, so a cached
        # value->suggestion pair could resurface a stale completion.
        # case_sensitive=True: Input paints suggestion[len(value):], which is
        # only coherent when the suggestion extends the EXACT typed value
        # (case_sensitive=False would hand us a casefolded value).
        super().__init__(use_cache=False, case_sensitive=True)
        self._commands = list(commands)
        self._flags = {name: list(options) for name, options in flags.items()}
        self._history = history

    async def get_suggestion(self, value: str) -> Optional[str]:
        return complete_line(
            value, commands=self._commands, flags=self._flags, history=self._history()
        )
