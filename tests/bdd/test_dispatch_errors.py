"""pytest-bdd bindings for the "dispatch_errors" flow.

Exercises dispatcher robustness against the REAL ``dispatch_command`` and the
``_COMMAND_HANDLERS`` registry from ``src/main.py``:

* an unknown command returns exit code 1 (and is genuinely absent from the
  registry);
* a known command whose underlying CLI method raises is caught by
  ``dispatch_command`` and returns 1 rather than propagating the exception;
* a known command routes to the correct handler (verified via real seeded
  output and a recorded Spotify-edge query, not a stub assertion);
* a handler that returns a non-zero code has that code propagated unchanged.

Shared fixtures (``cli``, ``fake_spotify``, ``seeded_repos``) come from
``tests/bdd/conftest.py`` and are reused verbatim. Extra fakes live here.
"""

from __future__ import annotations

import argparse
from typing import List

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from main import _COMMAND_HANDLERS, dispatch_command

scenarios("dispatch_errors.feature")


# ---------------------------------------------------------------------------
# Local fixtures (not shared — kept out of conftest per phase rules).
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatch_state() -> dict:
    """Mutable bag carrying state between Given/When/Then steps."""
    return {"rc": None, "raise_calls": 0}


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given("a seeded library with a wired CLI")
def _wired_cli(cli) -> None:
    # The shared ``cli`` fixture is already fully wired against the temp DB and
    # the FakeSpotify edge; requesting it here pins it into the scenario.
    assert cli is not None
    assert cli._spotify is not None


@given(
    parsers.parse('the command "{command}" is not in the registry'),
    target_fixture="unknown_command",
)
def _unknown_command(command: str) -> str:
    # Assert the precondition is real: the command truly is not registered.
    assert command not in _COMMAND_HANDLERS
    return command


@given('the "view" handler\'s CLI method raises an exception')
def _view_method_raises(cli, dispatch_state) -> None:
    def _boom(_playlist_name: str):
        dispatch_state["raise_calls"] += 1
        raise RuntimeError("simulated handler failure")

    # The real ``_handle_view`` calls ``cli.view_playlist(args.playlist)``, so
    # replacing the bound method makes the registered handler raise.
    cli.view_playlist = _boom


@given(parsers.parse('the "{command}" handler is replaced with one returning code {code:d}'))
def _replace_handler_with_code(command: str, code: int, monkeypatch) -> None:
    # Swap the registry entry so the handler returns a non-zero code without
    # raising; restored automatically by monkeypatch teardown.
    monkeypatch.setitem(_COMMAND_HANDLERS, command, lambda _cli, _args: code)


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse('the dispatcher runs the unknown command "{command}"'))
def _run_unknown(cli, dispatch_state, command: str) -> None:
    dispatch_state["rc"] = dispatch_command(cli, command, argparse.Namespace())


@when(parsers.parse('the dispatcher runs "view {playlist}"'))
def _run_view(cli, dispatch_state, fake_spotify, capsys, playlist: str) -> None:
    # Record which playlist names the Spotify edge is asked about so we can
    # prove routing reached the real handler body (not just returned 0).
    queried: List[str] = []
    original = fake_spotify.get_playlist_tracks

    def _recording_get_playlist_tracks(name: str):
        queried.append(name)
        return original(name)

    fake_spotify.get_playlist_tracks = _recording_get_playlist_tracks
    dispatch_state["queried_playlists"] = queried

    dispatch_state["rc"] = dispatch_command(cli, "view", argparse.Namespace(playlist=playlist))
    # Snapshot stdout immediately (the run helper does not capture).
    dispatch_state["stdout"] = capsys.readouterr().out


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse("the dispatcher returns exit code {code:d}"))
def _assert_rc(dispatch_state, code: int) -> None:
    assert dispatch_state["rc"] == code


@then("the raising method was invoked exactly once")
def _assert_raise_invoked(dispatch_state) -> None:
    assert dispatch_state["raise_calls"] == 1


@then("the process did not crash")
def _assert_no_crash(dispatch_state) -> None:
    # If the exception had escaped dispatch_command, the When step would have
    # raised and we would never reach here; the recorded int rc proves it was
    # caught and converted to a return code.
    assert isinstance(dispatch_state["rc"], int)


@then(parsers.parse('the playlist view output contains the seeded track "{track}"'))
def _assert_output_contains(dispatch_state, track: str) -> None:
    assert track in dispatch_state["stdout"]


@then(parsers.parse('the Spotify edge was queried for the "{playlist}" playlist'))
def _assert_edge_queried(dispatch_state, playlist: str) -> None:
    assert dispatch_state.get("queried_playlists") == [playlist]
