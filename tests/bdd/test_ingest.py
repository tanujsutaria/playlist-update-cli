"""BDD: the ``ingest`` flow.

Exercises ``PlaylistCLI.ingest_tracks`` (via the real ``ingest`` command parsed
by ``arg_parse`` and dispatched by ``dispatch_command``) for the ``liked`` and
``recent`` sources. ``ingest_tracks`` reaches the Spotify edge through
``self.spotify.sp.current_user_saved_tracks`` / ``current_user_recently_played``,
so we attach a deterministic fake ``.sp`` client to the shared ``fake_spotify``
double (the only external mock). Every assertion checks REAL SQLite state in
``repos.tracks`` -- row presence, ``status``, and total row counts -- never a
trivial truthy assertion.

Reuses the shared fixtures from ``tests/bdd/conftest.py`` (``seeded_repos``,
``fake_spotify``, ``cli``, ``run``) verbatim. The only local additions are a tiny
fake Spotify ``sp`` client and a per-scenario context dict; both live here so the
shared conftest stays untouched.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("ingest.feature")


# ---------------------------------------------------------------------------
# Local fakes / helpers (NOT in the shared conftest)
# ---------------------------------------------------------------------------


def _spotify_track(name: str, artist: str) -> Dict[str, Any]:
    """Build a Spotify-API-shaped track dict (the shape ``sp.*`` endpoints return)."""
    uri = f"spotify:track:{(artist + name).lower().replace(' ', '')[:22]}"
    return {
        "name": name,
        "uri": uri,
        "artists": [{"name": artist, "genres": [], "popularity": 50}],
        "album": {"name": f"{name} Album", "release_date": "2025-01-01"},
        "duration_ms": 180000,
        "explicit": False,
        "popularity": 42,
        "external_urls": {"spotify": f"https://open.spotify.com/track/{uri}"},
    }


class FakeSpClient:
    """Deterministic stand-in for ``SpotifyManager.sp`` (the spotipy client).

    Only the endpoints ``ingest_tracks`` calls are implemented. ``saved`` and
    ``recent`` hold the items each endpoint returns, paginated batches under 50
    so the ingest loop terminates after one page.
    """

    def __init__(self) -> None:
        self.saved: List[Dict[str, Any]] = []
        self.recent: List[Dict[str, Any]] = []

    def current_user_saved_tracks(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        if offset > 0:
            return {"items": []}
        return {"items": [{"track": t} for t in self.saved]}

    def current_user_recently_played(self, limit: int = 50) -> Dict[str, Any]:
        return {"items": [{"track": t, "played_at": "2025-05-01T00:00:00Z"} for t in self.recent]}


def _track_count(seeded_repos) -> int:
    row = seeded_repos.repos.conn.execute("SELECT COUNT(*) FROM tracks;").fetchone()
    return int(row[0])


@pytest.fixture
def ctx() -> Dict[str, Any]:
    """Per-scenario scratch space carried between Given/When/Then steps."""
    return {}


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given("a seeded library with 5 tracks")
def seeded_library(seeded_repos, fake_spotify, ctx) -> None:
    # Attach the fake spotipy client the CLI reaches via ``self.spotify.sp``.
    fake_spotify.sp = FakeSpClient()
    ctx["count_before"] = _track_count(seeded_repos)
    assert ctx["count_before"] == 5  # matches the shared seed


@given(parsers.parse("Spotify reports {n:d} liked tracks not yet in the store"))
def liked_new_tracks(fake_spotify, ctx, n: int) -> None:
    tracks = [_spotify_track(f"Liked Song {i}", f"Liked Artist {i}") for i in range(n)]
    fake_spotify.sp.saved = tracks
    ctx["expected_ids"] = [f"liked artist {i}|||liked song {i}" for i in range(n)]


@given(parsers.parse("Spotify reports {n:d} recently played track not yet in the store"))
def recent_new_tracks(fake_spotify, ctx, n: int) -> None:
    tracks = [_spotify_track(f"Recent Song {i}", f"Recent Artist {i}") for i in range(n)]
    fake_spotify.sp.recent = tracks
    ctx["expected_ids"] = [f"recent artist {i}|||recent song {i}" for i in range(n)]


@given("Spotify reports a liked track that already exists in the store")
def liked_existing_track(seeded_repos, fake_spotify, ctx) -> None:
    # Re-ingest the first seeded track (same artist/name -> same canonical id).
    artist, name = seeded_repos.artists[0], "First Light"
    fake_spotify.sp.saved = [_spotify_track(name, artist)]
    ctx["expected_ids"] = ["alpha artist|||first light"]
    assert seeded_repos.repos.tracks.get("alpha artist|||first light") is not None


@given("Spotify reports 1 valid liked track and 1 malformed liked track")
def liked_valid_and_malformed(fake_spotify, ctx) -> None:
    valid = _spotify_track("Valid Song", "Valid Artist")
    # Malformed: no artists list -> ingest_tracks skips it (no upsert called).
    malformed = {"name": "Orphan Song", "uri": "spotify:track:orphan", "artists": []}
    fake_spotify.sp.saved = [valid, malformed]
    ctx["expected_ids"] = ["valid artist|||valid song"]


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse('I run "{command}"'))
def run_command(run, ctx, command: str) -> None:
    ctx["rc"] = run(command)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then("the command succeeds")
def command_succeeds(ctx) -> None:
    assert ctx["rc"] == 0


@then("the new liked tracks are stored as candidates")
def new_liked_stored(seeded_repos, ctx) -> None:
    for track_id in ctx["expected_ids"]:
        row = seeded_repos.repos.tracks.get(track_id)
        assert row is not None, f"expected ingested track {track_id!r} in store"
        assert row["status"] == "candidate"


@then("the new recent track is stored as a candidate")
def new_recent_stored(seeded_repos, ctx) -> None:
    assert ctx["expected_ids"], "scenario should have set expected ids"
    for track_id in ctx["expected_ids"]:
        row = seeded_repos.repos.tracks.get(track_id)
        assert row is not None, f"expected ingested track {track_id!r} in store"
        assert row["status"] == "candidate"


@then(parsers.parse("the total track count increases by {delta:d}"))
def count_increases(seeded_repos, ctx, delta: int) -> None:
    assert _track_count(seeded_repos) == ctx["count_before"] + delta


@then("the total track count is unchanged")
def count_unchanged(seeded_repos, ctx) -> None:
    assert _track_count(seeded_repos) == ctx["count_before"]
