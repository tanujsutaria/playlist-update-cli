"""pytest-bdd binding for ``search.feature``.

Exercises the next-gen ``search`` flow end to end against the shared foundation
(real SQLite ``SongStore`` + fully-wired ``PlaylistCLI`` from the bdd conftest),
with ONLY the external provider boundary mocked.

The pipeline calls ``run_providers`` (imported into ``nextgen.pipeline`` as
``from .providers import run_providers``), which in turn shells out to
``web_search.run_deep_search`` (subprocess/network). We monkeypatch
``nextgen.pipeline.run_providers`` with a deterministic stub that returns a
canned :class:`nextgen.providers.ProviderRun`, so NO subprocess/network runs.
A module-level call counter on the real ``web_search.run_deep_search`` /
``nextgen.providers.run_providers`` guards that the true provider edge stays
untouched (it should never be invoked once the pipeline symbol is patched).

Assertions are real: CLI cache state (``cli.last_search_*``), the
``last_cached`` flag, and persisted row counts in ``search_runs`` /
``search_candidates`` / ``tracks`` in the temp SQLite database.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

import nextgen.pipeline as nextgen_pipeline
import nextgen.providers as nextgen_providers
import web_search
from nextgen.providers import ProviderRun

# Feature file resolved relative to bdd_features_base_dir (tests/bdd/features).
scenarios("search.feature")


# ---------------------------------------------------------------------------
# Local fixtures (kept LOCAL per phase rules -- do not touch the shared conftest)
# ---------------------------------------------------------------------------


@pytest.fixture
def state() -> dict:
    """Mutable per-scenario bag for passing values between steps."""
    return {}


@pytest.fixture
def provider_guard(monkeypatch: pytest.MonkeyPatch) -> Dict[str, int]:
    """Sentinel that fails loudly if the REAL provider edge ever runs.

    Replaces the true network/subprocess entry points with stubs that bump a
    counter and raise. If the pipeline is correctly intercepted at
    ``run_providers``, these are never called and the counters stay at 0.
    """
    calls: Dict[str, int] = {"run_deep_search": 0, "providers_run": 0}

    def _forbidden_deep_search(*args: Any, **kwargs: Any):  # pragma: no cover
        calls["run_deep_search"] += 1
        raise AssertionError("web_search.run_deep_search was called (network/subprocess leak)")

    def _forbidden_providers(*args: Any, **kwargs: Any):  # pragma: no cover
        calls["providers_run"] += 1
        raise AssertionError("nextgen.providers.run_providers (real) was called")

    monkeypatch.setattr(web_search, "run_deep_search", _forbidden_deep_search)
    monkeypatch.setattr(nextgen_providers, "run_providers", _forbidden_providers)
    return calls


def _canned_provider_run(results: List[Dict[str, Any]]) -> ProviderRun:
    return ProviderRun(
        results=results,
        providers=["mock-provider"],
        summary="Canned deterministic results (no network).",
        constraints={"mocked": True},
        policy={"path": "mock"},
    )


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given("a seeded library")
def _seeded_library(seeded_repos, state):
    # Materializes the temp DB + seed data; cli/run depend on it.
    state["seeded"] = seeded_repos


@given("the search provider returns canned results", target_fixture="canned_results")
def _provider_returns_canned(monkeypatch: pytest.MonkeyPatch, provider_guard, state):
    """Patch the symbol the pipeline actually calls with canned, novel tracks.

    These artists/songs are NOT in the seed set, so they prove the search flow
    inserts brand-new candidate rows.
    """
    canned = [
        {
            "song": "Neon Skyline",
            "artist": "Mock Wavelength",
            "year": "2021",
            "sources": ["https://example.test/a", "https://example.test/b"],
            "source_details": [
                {"url": "https://example.test/a", "title": "Review A", "snippet": "great"},
            ],
            "providers": ["mock-provider"],
        },
        {
            "song": "Glass Horizon",
            "artist": "Mock Wavelength",
            "year": "2022",
            "sources": ["https://example.test/c"],
            "providers": ["mock-provider"],
        },
        {
            "song": "Paper Tigers",
            "artist": "Stub Collective",
            "year": "2020",
            "sources": ["https://example.test/d"],
            "providers": ["mock-provider"],
        },
    ]
    captured = _canned_provider_run(canned)

    def _fake_run_providers(query: str, expanded: bool = False, on_progress=None) -> ProviderRun:
        state.setdefault("provider_query", query)
        state["provider_calls"] = state.get("provider_calls", 0) + 1
        return captured

    monkeypatch.setattr(nextgen_pipeline, "run_providers", _fake_run_providers)
    state["expected_candidate_count"] = len(canned)
    return canned


@given("the search provider returns no results")
def _provider_returns_empty(monkeypatch: pytest.MonkeyPatch, state):
    empty = _canned_provider_run([])

    def _fake_run_providers(query: str, expanded: bool = False, on_progress=None) -> ProviderRun:
        state["provider_calls"] = state.get("provider_calls", 0) + 1
        return empty

    monkeypatch.setattr(nextgen_pipeline, "run_providers", _fake_run_providers)


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse('I run the search command "{query}"'))
def _run_search(run, state, query):
    # The real arg parser handles `search <freeform query...>` (nargs="+").
    state["rc"] = run(f"search {query}")


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse("it exits with code {code:d}"))
def _exits_with(code, state):
    assert state["rc"] == code


@then("the CLI has cached search results")
def _cli_has_cached(cli, state):
    assert cli.last_search_results is not None
    expected = state["expected_candidate_count"]
    assert len(cli.last_search_results) == expected
    # Track ids and run id are populated alongside the result list.
    assert cli.last_search_track_ids is not None
    assert len(cli.last_search_track_ids) == expected
    assert cli.last_search_run_id is not None
    # The novel mocked tracks are surfaced (not the seed library).
    surfaced = {(r["song"], r["artist"]) for r in cli.last_search_results}
    assert ("Neon Skyline", "Mock Wavelength") in surfaced


@then("the cached results were freshly fetched")
def _results_fresh(cli):
    assert cli.last_search_cached is False


@then("the cached results were served from the cache")
def _results_cached(cli):
    assert cli.last_search_cached is True


@then("no provider subprocess or network call was made")
def _no_network(provider_guard, state):
    # The real edges were replaced with raising sentinels; they must be untouched.
    assert provider_guard["run_deep_search"] == 0
    assert provider_guard["providers_run"] == 0
    # The mocked pipeline boundary, however, was exercised.
    assert state.get("provider_calls", 0) >= 1


@then("the search run is recorded in the database")
def _run_recorded(seeded_repos, cli, state):
    conn = seeded_repos.repos.conn
    row = conn.execute("SELECT COUNT(*) FROM search_runs WHERE status = 'ok';").fetchone()
    assert row[0] >= 1
    # The CLI's recorded run id exists as a row.
    exists = conn.execute(
        "SELECT 1 FROM search_runs WHERE run_id = ?;",
        (cli.last_search_run_id,),
    ).fetchone()
    assert exists is not None


@then("the candidate tracks are persisted in the database")
def _candidates_persisted(seeded_repos, cli, state):
    conn = seeded_repos.repos.conn
    expected = state["expected_candidate_count"]
    cand_count = conn.execute(
        "SELECT COUNT(*) FROM search_candidates WHERE run_id = ?;",
        (cli.last_search_run_id,),
    ).fetchone()[0]
    assert cand_count == expected
    # The novel candidate tracks were written into the tracks table.
    for track_id in cli.last_search_track_ids:
        row = conn.execute(
            "SELECT status FROM tracks WHERE track_id = ?;",
            (track_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "candidate"


@then("only one search run is recorded in the database")
def _single_run(seeded_repos):
    conn = seeded_repos.repos.conn
    count = conn.execute("SELECT COUNT(*) FROM search_runs;").fetchone()[0]
    assert count == 1


@then("the CLI has no cached search results")
def _cli_no_cached(cli, state):
    assert cli.last_search_results is None
    assert cli.last_search_track_ids is None
    assert cli.last_search_run_id is None
    # The empty-provider path was still invoked (so the flow truly ran).
    assert state.get("provider_calls", 0) >= 1
