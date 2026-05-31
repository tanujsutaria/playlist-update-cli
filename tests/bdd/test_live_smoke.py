"""OPT-IN live Spotify smoke test (SKIPPED by default).

Every other BDD module mocks the Spotify edge. This one does the opposite: it
constructs the REAL :class:`spotify_manager.SpotifyManager`, authenticates against
the live Spotify API, and performs a strictly READ-ONLY check (it lists the
account's playlists). It never creates, refreshes, or mutates any playlist.

It is skipped unless BOTH conditions hold:

  * the env flag ``RUN_LIVE_SPOTIFY=1`` is set, AND
  * real credentials are present (``SPOTIFY_CLIENT_ID`` -- loaded either from the
    process environment or from ``config/.env`` at import time).

So a normal ``make test`` / ``make ci`` run collects this file but skips the
scenario, keeping the suite fully offline and deterministic.

How to run it locally::

    # 1. Put real creds in config/.env (see config/.env.example) or export them:
    #      export SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=...
    #      export SPOTIFY_REDIRECT_URI=http://localhost:8888/callback
    # 2. Run via the Makefile target (sets RUN_LIVE_SPOTIFY=1 for you):
    make test-live
    # or directly:
    RUN_LIVE_SPOTIFY=1 .venv/bin/python -m pytest tests/bdd/test_live_smoke.py -m live -v

The first run opens a browser for the OAuth consent; the token is then cached in
``.spotify_cache`` for subsequent runs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

# Load config/.env (if present) BEFORE evaluating the skip guard, so credentials
# stored there -- not just exported env vars -- enable the live run. This mirrors
# how PlaylistCLI.__init__ loads them in src/main.py.
try:
    from dotenv import load_dotenv

    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    load_dotenv(_PROJECT_ROOT / "config" / ".env")
except Exception:  # pragma: no cover - dotenv is a hard dep, this is belt+braces
    pass


# Gate: opt-in flag AND real creds must both be present, else skip the whole file.
_RUN_LIVE = bool(os.getenv("RUN_LIVE_SPOTIFY")) and bool(os.getenv("SPOTIFY_CLIENT_ID"))

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "live Spotify smoke test is opt-in: set RUN_LIVE_SPOTIFY=1 and provide "
            "SPOTIFY_CLIENT_ID (and secret/redirect) via env or config/.env "
            "(e.g. `make test-live`)"
        ),
    ),
]

scenarios("live.feature")


@pytest.fixture
def live_state() -> dict:
    """Per-scenario bag carrying the constructed manager + listed playlists."""
    return {}


@given("live Spotify credentials are available")
def _creds_available() -> None:
    # The skipif guard already proved RUN_LIVE_SPOTIFY + SPOTIFY_CLIENT_ID are set;
    # re-assert here so the precondition is explicit if the scenario ever runs.
    assert os.getenv("RUN_LIVE_SPOTIFY")
    assert os.getenv("SPOTIFY_CLIENT_ID")


@when("I construct a real SpotifyManager")
def _construct_manager(live_state) -> None:
    # Imported lazily so collection never touches spotipy auth when skipped.
    from spotify_manager import SpotifyManager

    # Real constructor: performs OAuth, calls current_user(), and loads playlists.
    live_state["manager"] = SpotifyManager()


@then("it has an authenticated user id")
def _has_user_id(live_state) -> None:
    mgr = live_state["manager"]
    assert isinstance(mgr.user_id, str)
    assert mgr.user_id  # non-empty


@then("it can list the account playlists without modifying anything")
def _can_list_playlists(live_state) -> None:
    mgr = live_state["manager"]
    # _load_playlists() ran in the constructor; `playlists` is a name -> id map.
    # READ-ONLY: we only assert its type/shape. No create/refresh/remove is ever
    # invoked, so the live account is never modified.
    assert isinstance(mgr.playlists, dict)
    for name, playlist_id in mgr.playlists.items():
        assert isinstance(name, str)
        assert isinstance(playlist_id, str)
