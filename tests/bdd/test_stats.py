"""pytest-bdd binding for the proving ``stats.feature`` scenario.

Exercises the full shared foundation: a real SQLite-backed ``SongStore`` seeded
via ``seeded_repos``, a fully-wired ``PlaylistCLI``, and the real ``stats``
command dispatched through ``arg_parse.parse_tokens`` + ``dispatch_command``.
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# Feature file resolved relative to bdd_features_base_dir (tests/bdd/features).
scenarios("stats.feature")


@pytest.fixture
def state() -> dict:
    """Mutable per-scenario bag for passing values between steps."""
    return {}


@given("a seeded library")
def _seeded_library(seeded_repos, state):
    # seeded_repos materializes the temp DB + seed data; cli/run depend on it.
    state["seeded"] = seeded_repos


@when("I run the stats command")
def _run_stats(run, state):
    state["rc"] = run("stats")


@then(parsers.parse("it exits with code {code:d}"))
def _exits_with(code, state):
    assert state["rc"] == code


@then("the database reports the seeded track count")
def _track_count(seeded_repos, state):
    stats = seeded_repos.store.get_stats()
    assert stats["total_songs"] == len(seeded_repos.track_ids)
