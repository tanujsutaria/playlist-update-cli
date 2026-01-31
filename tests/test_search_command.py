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
    monkeypatch.setattr(mock_cli.__class__, "search_pipeline", property(lambda self: type("P", (), {"run": fake_run})()))

    mock_cli.search_songs("late night jazz")

    assert mock_cli.last_search_query == "late night jazz"
    assert mock_cli.last_search_results[0]["song"] == "Track A"
    assert mock_cli.last_search_results[0]["track_id"] == "artist 1|||track a"
