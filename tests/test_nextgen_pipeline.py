from __future__ import annotations

import pytest

from nextgen import pipeline as pipeline_mod
from nextgen.pipeline import SearchPipeline, _extract_year_target
from nextgen.providers import ProviderRun
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories


def test_extract_year_target_two_digit_decades_not_future():
    # Two-digit decades resolve to the midpoint of the decade. The 20xx
    # reading is preferred only when it is not in the future; otherwise the
    # 19xx reading is used. These hold for any current year >= 2025.
    assert _extract_year_target("80s music") == 1985
    assert _extract_year_target("90s britpop") == 1995
    assert _extract_year_target("roaring 20s") == 2025
    assert _extract_year_target("30s swing") == 1935
    assert _extract_year_target("40s jazz") == 1945


def test_extract_year_target_four_digit_decade():
    assert _extract_year_target("indie 2010s") == 2015
    assert _extract_year_target("1990s shoegaze") == 1995


def test_extract_year_target_bare_year():
    assert _extract_year_target("released in 1999") == 1999
    assert _extract_year_target("late night jazz from 1998") == 1998


def test_extract_year_target_none_when_absent():
    assert _extract_year_target("uplifting acoustic folk") is None


# ---------------------------------------------------------------------------
# Progress threading: pipeline.run hands its `progress` callback straight down
# to run_providers as `on_progress`, so provider-level updates ("providers
# k/N") ride the same string channel as the coarse stage names.
# ---------------------------------------------------------------------------


@pytest.fixture
def repos(tmp_path):
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    return Repositories(conn)


def _empty_provider_run() -> ProviderRun:
    return ProviderRun(results=[], providers=[], summary="", constraints={}, policy={})


class TestRunProgressThreading:
    def test_progress_forwarded_as_on_progress(self, repos, monkeypatch):
        captured = {}

        def fake_run_providers(query, expanded=False, on_progress=None):
            captured["on_progress"] = on_progress
            return _empty_provider_run()

        monkeypatch.setattr(pipeline_mod, "run_providers", fake_run_providers)
        stages = []

        def _cb(stage: str) -> None:
            stages.append(stage)

        SearchPipeline(repos).run("progress threading query", progress=_cb)

        assert captured["on_progress"] is _cb
        # The coarse stage names still flow through the same callback.
        assert "search" in stages
        assert "extract" in stages

    def test_no_progress_passes_none(self, repos, monkeypatch):
        """Backward compat: run() without progress hands run_providers None."""
        captured = {}

        def fake_run_providers(query, expanded=False, on_progress=None):
            captured["on_progress"] = on_progress
            return _empty_provider_run()

        monkeypatch.setattr(pipeline_mod, "run_providers", fake_run_providers)

        SearchPipeline(repos).run("no progress query")

        assert captured["on_progress"] is None
