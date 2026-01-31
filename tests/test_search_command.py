"""
Unit tests for the search command workflow.
"""
import web_search


def test_search_sets_last_results(monkeypatch, mock_cli):
    results = [
        {
            "song": "Track A",
            "artist": "Artist 1",
            "year": "2020",
            "why": "fits mood",
            "sources": [],
            "metrics": {},
        }
    ]

    def fake_run_deep_search(query, expanded=False):
        return (
            results,
            {},
            ["codex"],
            None,
            [],
            "summary",
            {},
            {"path": "hybrid", "expanded": expanded},
        )

    monkeypatch.setattr(web_search, "run_deep_search", fake_run_deep_search)
    monkeypatch.setattr(mock_cli, "_attach_spotify_urls", lambda items: None)

    mock_cli.search_songs("late night jazz")

    assert mock_cli.last_search_results == results
    assert mock_cli.last_search_query == "late night jazz"
    assert mock_cli.last_search_summary == "summary"
