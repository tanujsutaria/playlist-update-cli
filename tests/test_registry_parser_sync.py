"""Drift rail: the argparse parser and the handler registry stay in lockstep.

The command inventory is maintained in multiple places (parser, registry,
help groups, TUI metadata). This pins the two load-bearing ones to exact
equality, so adding a command to one without the other fails CI instead of
surfacing as "unknown command" at runtime.
"""

from __future__ import annotations

import argparse

import main
from arg_parse import setup_parsers


def _parser_subcommands() -> set:
    parser = setup_parsers()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    return set(subparsers.choices)


def test_every_parser_command_has_a_handler():
    missing = sorted(_parser_subcommands() - set(main._COMMAND_HANDLERS))
    assert not missing, f"parser subcommands with no _COMMAND_HANDLERS entry: {missing}"


def test_every_handler_has_a_parser_command():
    missing = sorted(set(main._COMMAND_HANDLERS) - _parser_subcommands())
    assert not missing, f"_COMMAND_HANDLERS entries with no parser subcommand: {missing}"


def test_registry_is_the_same_object_in_main_and_dispatch():
    """main re-exports the registry as an ALIAS, never a copy.

    tests (and the bdd suite) mutate the registry via
    monkeypatch.setitem(main._COMMAND_HANDLERS, ...) while dispatch_command
    reads commands.dispatch._COMMAND_HANDLERS — a copy would make those
    patches silent no-ops."""
    import commands.dispatch

    assert main._COMMAND_HANDLERS is commands.dispatch._COMMAND_HANDLERS
    assert main.dispatch_command is commands.dispatch.dispatch_command
