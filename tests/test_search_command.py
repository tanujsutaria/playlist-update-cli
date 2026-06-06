"""Unit tests for the search command workflow."""

from nextgen.pipeline import SearchResult


def test_search_sets_last_results(monkeypatch, mock_cli):
    results = [
        SearchResult(
            track_id="artist 1|||track a",
            song="Track A",
            artist="Artist 1",
            year="2020",
            score=0.9,
            strict_ratio=0.8,
            sources=["source-1"],
            providers=["codex"],
        )
    ]

    def fake_run(self, query, expanded=False, progress=None, on_result=None):
        return results, "run-1"

    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(
        mock_cli.__class__,
        "search_pipeline",
        property(lambda self: type("P", (), {"run": fake_run})()),
    )

    mock_cli.search_songs("late night jazz")

    assert mock_cli.last_search_query == "late night jazz"
    assert mock_cli.last_search_results[0]["song"] == "Track A"
    assert mock_cli.last_search_results[0]["track_id"] == "artist 1|||track a"


def test_search_resets_state_on_error(monkeypatch, mock_cli):
    """When the search pipeline raises, ALL search state fields must be cleared."""
    # Set up some stale state from a previous search
    mock_cli.last_search_query = "previous query"
    mock_cli.last_search_results = [{"song": "Stale"}]
    mock_cli.last_search_run_id = "old-run"
    mock_cli.last_search_track_ids = ["old-track"]
    mock_cli.last_search_summary = "old summary"
    mock_cli.last_search_metrics = ["metric"]
    mock_cli.last_search_constraints = {"key": "val"}
    mock_cli.last_search_expanded = True
    mock_cli.last_search_policy = {"tier": 1}
    mock_cli.last_search_cached = True

    class FailPipeline:
        def run(self, query, expanded=False, progress=None, on_result=None):
            raise RuntimeError("search failed")

    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(
        mock_cli.__class__, "search_pipeline", property(lambda self: FailPipeline())
    )

    mock_cli.search_songs("new query")

    # All state fields should be reset
    assert mock_cli.last_search_results is None
    assert mock_cli.last_search_query is None
    assert mock_cli.last_search_summary is None
    assert mock_cli.last_search_metrics is None
    assert mock_cli.last_search_constraints is None
    assert mock_cli.last_search_expanded is False
    assert mock_cli.last_search_policy is None
    assert mock_cli.last_search_run_id is None
    assert mock_cli.last_search_track_ids is None
    assert mock_cli.last_search_cached is False


def test_search_resets_state_on_empty_results(monkeypatch, mock_cli):
    """When pipeline returns empty results, search state should be fully reset."""
    mock_cli.last_search_results = [{"song": "Stale"}]
    mock_cli.last_search_run_id = "old-run"

    def fake_run(self, query, expanded=False, progress=None, on_result=None):
        return [], "run-empty"

    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(
        mock_cli.__class__,
        "search_pipeline",
        property(lambda self: type("P", (), {"run": fake_run})()),
    )

    mock_cli.search_songs("empty results query")

    assert mock_cli.last_search_results is None
    assert mock_cli.last_search_run_id is None


def test_search_with_list_query(monkeypatch, mock_cli):
    """search_songs should join list queries into a string."""
    results = [
        SearchResult(
            track_id="a|||b",
            song="B",
            artist="A",
            year="2023",
            score=0.5,
            strict_ratio=0.5,
            sources=[],
            providers=["codex"],
        )
    ]

    def fake_run(self, query, expanded=False, progress=None, on_result=None):
        return results, "run-list"

    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(
        mock_cli.__class__,
        "search_pipeline",
        property(lambda self: type("P", (), {"run": fake_run})()),
    )

    mock_cli.search_songs(["indie", "rock", "2023"])

    assert mock_cli.last_search_query == "indie rock 2023"


def _fake_pipeline(results, **attrs):
    """A stand-in search_pipeline exposing run() + the run-level scalar attrs
    that search_songs reads via getattr (last_cached/last_summary/...)."""
    defaults = {
        "last_cached": False,
        "last_summary": None,
        "last_constraints": {},
        "last_requested_metrics": [],
    }
    defaults.update(attrs)

    def run(self, query, expanded=False, progress=None, on_result=None):
        return results, "run-x"

    return type("P", (), {**defaults, "run": run})


def test_search_applies_monthly_listener_constraint(monkeypatch, mock_cli):
    """The '<10k monthly listeners' constraint drops over-limit rows on the
    display path, and the summary/metrics/constraints state is populated."""
    results = [
        SearchResult(
            track_id="a|||over",
            song="Over",
            artist="A",
            year="2020",
            score=0.9,
            strict_ratio=0.8,
            sources=[],
            providers=["claude"],
            metrics={"monthly_listeners": 50000},
        ),
        SearchResult(
            track_id="b|||under",
            song="Under",
            artist="B",
            year="2021",
            score=0.8,
            strict_ratio=0.8,
            sources=[],
            providers=["claude"],
            metrics={"monthly_listeners": 5000},
        ),
    ]
    fake = _fake_pipeline(
        results,
        last_summary="why these",
        last_constraints={"max_monthly_listeners": 10000},
        last_requested_metrics=["monthly_listeners"],
    )
    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(mock_cli.__class__, "search_pipeline", property(lambda self: fake()))

    mock_cli.search_songs("songs under 10000 monthly listeners")

    assert [r["song"] for r in mock_cli.last_search_results] == ["Under"]
    assert mock_cli.last_search_track_ids == ["b|||under"]
    assert mock_cli.last_search_summary == "why these"
    assert mock_cli.last_search_metrics == ["monthly_listeners"]
    assert mock_cli.last_search_constraints == {"max_monthly_listeners": 10000}


def test_search_keeps_unverified_rows_when_metrics_absent(monkeypatch, mock_cli):
    """Lenient policy: a constrained query keeps rows that carry no metric to
    verify, rather than nuking a sparse provider response to empty."""
    results = [
        SearchResult(
            track_id="a|||x",
            song="X",
            artist="A",
            year="2020",
            score=0.9,
            strict_ratio=0.8,
            sources=[],
            providers=["claude"],
            metrics={},
        ),
        SearchResult(
            track_id="b|||y",
            song="Y",
            artist="B",
            year="2021",
            score=0.8,
            strict_ratio=0.8,
            sources=[],
            providers=["claude"],
            metrics={},
        ),
    ]
    fake = _fake_pipeline(
        results,
        last_constraints={"max_monthly_listeners": 10000},
        last_requested_metrics=["monthly_listeners"],
    )
    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(mock_cli.__class__, "search_pipeline", property(lambda self: fake()))

    mock_cli.search_songs("songs under 10000 monthly listeners")

    assert {r["song"] for r in mock_cli.last_search_results} == {"X", "Y"}


def _sr(track_id, metrics):
    return SearchResult(
        track_id=track_id,
        song=track_id,
        artist="A",
        year="2020",
        score=0.5,
        strict_ratio=0.5,
        sources=[],
        providers=["claude"],
        metrics=metrics,
    )


class TestApplyMetricConstraints:
    """Unit tests for the pure display-path filter + its stats accounting."""

    def test_missing_listener_metric_but_similarity_fail_is_dropped_not_unverified(self, mock_cli):
        # Row lacks monthly_listeners (would be 'unverified') AND fails similarity
        # (0.5 < 0.55 default) -> must count as dropped ONCE, never also unverified.
        rows = [_sr("a", {"similarity": 0.5})]
        kept, stats = mock_cli._apply_metric_constraints(
            rows, {"max_monthly_listeners": 10000, "similarity_requested": True}
        )
        assert kept == []
        assert stats == {"kept": 0, "dropped": 1, "unverified": 0}

    def test_missing_similarity_metric_kept_and_flagged_unverified(self, mock_cli):
        # Passes the listener bound, but the similarity metric is absent -> kept,
        # and counted unverified (symmetric with the monthly_listeners branch).
        rows = [_sr("a", {"monthly_listeners": 5000})]
        kept, stats = mock_cli._apply_metric_constraints(
            rows, {"max_monthly_listeners": 10000, "similarity_requested": True}
        )
        assert [r.track_id for r in kept] == ["a"]
        assert stats == {"kept": 1, "dropped": 0, "unverified": 1}

    def test_zero_bound_is_honoured(self, mock_cli):
        # max_monthly_listeners=0 is a real (if odd) bound, not "no constraint".
        rows = [_sr("a", {"monthly_listeners": 5})]
        kept, stats = mock_cli._apply_metric_constraints(rows, {"max_monthly_listeners": 0})
        assert kept == []
        assert stats["dropped"] == 1

    def test_no_constraints_passthrough(self, mock_cli):
        rows = [_sr("a", {}), _sr("b", {})]
        kept, stats = mock_cli._apply_metric_constraints(rows, {})
        assert kept == rows
        assert stats == {"kept": 0, "dropped": 0, "unverified": 0}
