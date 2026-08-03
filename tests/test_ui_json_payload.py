"""Contract tests for ui.json_payload — the handlers' --json block.

Pins the exact semantics the copy-pasted try/finally blocks had, so handler
conversions to the context manager are provably faithful: emit-on-every-exit
(including exceptions and early returns), None -> JSON null, and the mode
always resetting so it can never leak into the next command.
"""

from __future__ import annotations

import json

import pytest

import ui


@pytest.fixture(autouse=True)
def _reset_json_mode():
    ui.set_json_mode(False)
    yield
    ui.set_json_mode(False)


def test_enabled_emits_payload_and_resets(capsys):
    with ui.json_payload(True) as out:
        assert ui.is_json_mode()
        out["payload"] = {"answer": 42}
    assert not ui.is_json_mode()
    assert json.loads(capsys.readouterr().out) == {"answer": 42}


def test_enabled_emits_null_when_nothing_assigned(capsys):
    """The None-on-error convention: a payload never set emits as JSON null."""
    with ui.json_payload(True):
        pass
    assert json.loads(capsys.readouterr().out) is None


def test_enabled_emits_even_on_exception(capsys):
    """A crash mid-handler still emits the payload captured so far and resets
    the mode — the dispatch backstop turns the exception into rc=1 afterwards."""
    with pytest.raises(RuntimeError):
        with ui.json_payload(True) as out:
            out["payload"] = {"partial": True}
            raise RuntimeError("boom")
    assert not ui.is_json_mode()
    assert json.loads(capsys.readouterr().out) == {"partial": True}


def test_enabled_emits_on_early_return(capsys):
    """Handlers return exit codes from inside the with-body (/similar, /pull)."""

    def handler() -> int:
        with ui.json_payload(True) as out:
            out["payload"] = {"early": True}
            return 1

    assert handler() == 1
    assert json.loads(capsys.readouterr().out) == {"early": True}


def test_disabled_emits_nothing_and_stays_reset(capsys):
    with ui.json_payload(False) as out:
        assert not ui.is_json_mode()
        out["payload"] = {"answer": 42}
    assert not ui.is_json_mode()
    assert capsys.readouterr().out == ""
