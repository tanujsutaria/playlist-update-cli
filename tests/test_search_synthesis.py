"""End-to-end tests for the deep-search synthesis/constraints re-threading.

Exercises the real SearchPipeline against a temp SQLite DB, monkeypatching only
the network boundary (web_search.run_deep_search, imported into nextgen.providers).
Offline: tests/conftest.py stubs sentence_transformers, so EmbeddingModel is
deterministic and instant.

Covers the three regressions the fix closes:
  1. fresh run surfaces summary + constraints + requested_metrics + per-item metrics;
  2. a cache hit rehydrates summary (search_runs.summary) and metrics
     (search_candidates.metrics_json) and recomputes requested_metrics;
  3. a score-config rescore preserves the persisted metrics_json (the ON-CONFLICT
     trap — metrics_json is INSERT-only, never in DO UPDATE SET).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from nextgen import providers
from nextgen.pipeline import SearchPipeline
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories

# Deliberately avoids "like"/"similar" so the query implies only monthly_listeners
# (no similarity), keeping the canned fresh requested_metrics self-consistent with
# the cache-path recomputation in _requested_metrics_for_query.
QUERY = "obscure post-punk songs under 10000 monthly listeners"
SUMMARY = "Why these fit: taut post-punk, all under the 10k listener ceiling."


def _canned_deep_search(result_metrics: Dict[str, Any]):
    """A run_deep_search stand-in returning the 8-tuple shape, one result."""

    def _fake(query: str, expanded: bool = False, **kwargs):
        return (
            [
                {
                    "song": "Glass Diary",
                    "artist": "Tamaryn",
                    "year": "2010",
                    "sources": ["https://example.com/1"],
                    "providers": ["claude"],
                    "metrics": result_metrics,
                }
            ],
            {"claude": []},
            ["claude"],
            None,
            ["monthly_listeners"],  # index 4 — requested_metrics
            SUMMARY,
            {"max_monthly_listeners": 10000},
            {"expanded": expanded},
        )

    return _fake


@pytest.fixture
def repos(tmp_path):
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    return Repositories(conn)


def test_fresh_run_surfaces_summary_constraints_metrics(repos, monkeypatch):
    monkeypatch.setattr(
        providers, "run_deep_search", _canned_deep_search({"monthly_listeners": 5000})
    )
    pipe = SearchPipeline(repos)

    results, run_id = pipe.run(QUERY)

    assert pipe.last_summary == SUMMARY
    assert pipe.last_constraints.get("max_monthly_listeners") == 10000
    assert pipe.last_requested_metrics == ["monthly_listeners"]
    assert len(results) == 1
    assert results[0].metrics == {"monthly_listeners": 5000}


def test_cache_hit_rehydrates_summary_and_metrics(repos, monkeypatch):
    monkeypatch.setattr(
        providers, "run_deep_search", _canned_deep_search({"monthly_listeners": 5000})
    )
    pipe = SearchPipeline(repos)
    pipe.run(QUERY)  # fresh: persists summary + metrics_json

    # A new pipeline over the SAME repos: must hit cache and rehydrate from disk.
    pipe2 = SearchPipeline(repos)
    results2, _ = pipe2.run(QUERY)

    assert pipe2.last_cached is True
    assert pipe2.last_summary == SUMMARY  # from search_runs.summary
    # Recomputed from the query text (mirrors web_search); contains the listener metric.
    assert "monthly_listeners" in pipe2.last_requested_metrics
    assert len(results2) == 1
    assert results2[0].metrics == {"monthly_listeners": 5000}  # from search_candidates.metrics_json


def test_rescore_preserves_persisted_metrics(repos, monkeypatch):
    """A score-config change triggers _rescore_cached_run, which re-upserts
    candidates with no metrics payload. metrics_json must survive (INSERT-only)."""
    monkeypatch.setattr(
        providers, "run_deep_search", _canned_deep_search({"monthly_listeners": 5000})
    )
    SearchPipeline(repos, strict_threshold=0.6).run(QUERY)  # fresh, config A

    # Different threshold -> different score_config_hash -> rescore on cache hit.
    pipe_b = SearchPipeline(repos, strict_threshold=0.8)
    results_b, run_id = pipe_b.run(QUERY)

    # The rescore did not null the metrics, in-memory or on disk.
    assert results_b[0].metrics == {"monthly_listeners": 5000}
    rows: List[Any] = repos.conn.execute(
        "SELECT metrics_json FROM search_candidates WHERE run_id = ?;", (run_id,)
    ).fetchall()
    assert rows
    assert all(r[0] and "monthly_listeners" in r[0] for r in rows)
