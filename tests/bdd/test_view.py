"""pytest-bdd bindings for ``view.feature``.

The ``view`` command lists a playlist's tracks. These scenarios exercise:

* viewing an existing playlist (the shared ``FakeSpotify`` double) and asserting
  the captured stdout actually contains the seeded track rows;
* viewing a MISSING playlist, asserting the command still exits 0 and prints no
  track rows (``view_playlist`` swallows + reports gracefully);
* the regression we fixed: a playlist that exists only because
  ``SpotifyManager._load_playlists`` paginates through ALL pages, combined with
  CASE-INSENSITIVE name matching ("favorites" -> "Favorites"). This drives a
  REAL ``SpotifyManager`` (built via ``__new__``) against a small LOCAL fake
  ``.sp`` client defined in this module, so the real pagination + ``_resolve_name``
  code runs. No shared files are edited.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from main import dispatch_command
from spotify_manager import SpotifyManager

# Resolved relative to bdd_features_base_dir (tests/bdd/features) in pyproject.
scenarios("view.feature")


# ---------------------------------------------------------------------------
# Local fakes (NOT in the shared conftest — pagination is specific to this flow).
# ---------------------------------------------------------------------------


class _FakeSpClient:
    """Minimal fake of the ``spotipy.Spotify`` client surface ``SpotifyManager``
    touches for loading playlists and reading tracks.

    Splits the user's playlists across TWO pages so the real ``_load_playlists``
    pagination loop (``current_user_playlists`` -> ``next``) is required to ever
    see the playlist that lives on the second page.
    """

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id
        owner = {"id": user_id}
        # "Favorites" only appears on page 2 -> only reachable via pagination.
        self._pages: List[Dict[str, Any]] = [
            {
                "items": [
                    {"name": "Page One Mix", "id": "pl_page1", "owner": owner},
                ],
                "next": "cursor-2",
            },
            {
                "items": [
                    {"name": "Favorites", "id": "pl_favorites", "owner": owner},
                ],
                "next": None,
            },
        ]
        self.pages_fetched = 0

    def current_user_playlists(self, limit: int = 50) -> Dict[str, Any]:
        self.pages_fetched += 1
        return self._pages[0]

    def next(self, results: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if results.get("next") == "cursor-2":
            self.pages_fetched += 1
            return self._pages[1]
        return None

    def playlist_tracks(self, playlist_id: str, fields: Optional[str] = None) -> Dict[str, Any]:
        assert playlist_id == "pl_favorites", "tracks fetched for the wrong playlist id"
        return {
            "items": [
                {
                    "added_at": "2024-03-01T00:00:00Z",
                    "track": {
                        "name": "Paginated Anthem",
                        "uri": "spotify:track:paginated1",
                        "artists": [{"name": "Page Two Artist"}],
                    },
                },
            ],
            "next": None,
        }


@pytest.fixture
def state() -> dict:
    """Mutable per-scenario bag for passing values between steps."""
    return {}


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given("a seeded library")
def _seeded_library(seeded_repos, state):
    # Materialize the temp DB + seed data; cli/run/fake_spotify depend on it.
    state["seeded"] = seeded_repos


@given(
    parsers.parse('a real Spotify manager whose "{name}" playlist lives on a later page'),
    target_fixture="paginated_manager",
)
def _real_manager_paginated(name, cli, state):
    """Build a REAL SpotifyManager (no __init__) backed by the local fake client
    and wire it onto the existing ``cli`` so the view flow uses real pagination.
    """
    mgr = SpotifyManager.__new__(SpotifyManager)
    mgr.user_id = "test_user_id"
    mgr.sp = _FakeSpClient(mgr.user_id)
    mgr.playlists = {}
    mgr._load_playlists()  # real pagination loop fills the cache from both pages
    # Sanity: the later-page playlist landed in the cache under its real name.
    assert name in mgr.playlists
    cli._spotify = mgr
    state["manager"] = mgr
    state["cli"] = cli
    return mgr


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse('I run the view command for "{name}"'))
def _run_view(run, name, capsys, state):
    state["rc"] = run(f"view {name}")
    state["out"] = capsys.readouterr().out


@when(parsers.parse('I view the playlist using the lowercase name "{name}"'))
def _view_lowercase(name, capsys, state):
    cli = state["cli"]
    # Go through the REAL dispatcher with the lowercase name so case-insensitive
    # resolution (_resolve_name) is what makes this work; capture its rc + stdout.
    args = type("ViewArgs", (), {"playlist": name})()
    state["rc"] = dispatch_command(cli, "view", args)
    state["out"] = capsys.readouterr().out


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then(parsers.parse("it exits with code {code:d}"))
def _exits_with(code, state):
    assert state["rc"] == code


@then("the playlist tracks are printed to output")
def _tracks_printed(fake_spotify, state):
    out = state["out"]
    # The shared FakeSpotify seeds the first two tracks onto "Favorites".
    expected = fake_spotify.get_playlist_tracks("Favorites")
    assert expected, "fixture precondition: Favorites should have tracks"
    for track in expected:
        assert track["name"] in out, f"expected track name {track['name']!r} in view output"
    # The view footer reports the total it rendered.
    assert f"Total tracks: {len(expected)}" in out


@then("no track rows are printed to output")
def _no_rows(fake_spotify, state):
    out = state["out"]
    # An unknown playlist yields [] tracks; none of the seeded names appear and
    # the populated "Total tracks:" footer is never emitted.
    assert "Total tracks:" not in out
    for track in fake_spotify.get_playlist_tracks("Favorites"):
        assert track["name"] not in out


@then("every page of the paginated playlists was fetched")
def _all_pages_fetched(state):
    mgr = state["manager"]
    # Two pages exist; both must have been requested (page1 + next()).
    assert mgr.sp.pages_fetched == 2
    assert mgr.playlists == {"Page One Mix": "pl_page1", "Favorites": "pl_favorites"}


@then("the paginated playlist tracks are printed to output")
def _paginated_tracks_printed(state):
    out = state["out"]
    assert "Paginated Anthem" in out
    assert "Page Two Artist" in out
    assert "Total tracks: 1" in out
