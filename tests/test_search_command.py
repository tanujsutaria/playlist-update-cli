"""Unit tests for the search command workflow."""
from unittest.mock import MagicMock

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
    monkeypatch.setattr(mock_cli.__class__, "search_pipeline", property(lambda self: type("P", (), {"run": fake_run})()))

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
    monkeypatch.setattr(mock_cli.__class__, "search_pipeline", property(lambda self: FailPipeline()))

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
    monkeypatch.setattr(mock_cli.__class__, "search_pipeline", property(lambda self: type("P", (), {"run": fake_run})()))

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
    monkeypatch.setattr(mock_cli.__class__, "search_pipeline", property(lambda self: type("P", (), {"run": fake_run})()))

    mock_cli.search_songs(["indie", "rock", "2023"])

    assert mock_cli.last_search_query == "indie rock 2023"
