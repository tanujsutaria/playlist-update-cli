import os

from web_search import detect_search_commands, synthesize_results, extract_constraints, extract_requested_metrics


def test_detect_search_commands_prefers_explicit(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("WEB_SEARCH_CLAUDE_CMD", "claude --json")
    monkeypatch.setenv("WEB_SEARCH_CODEX_CMD", "codex --json")

    commands = detect_search_commands(os.environ)

    assert commands["claude"] == "claude --json"
    assert commands["codex"] == "codex --json"


def test_synthesize_results_merges_providers():
    provider_results = {
        "claude": [
            {"song": "Track A", "artist": "Artist 1", "why": "fits mood", "sources": ["https://example.com/a"]},
            {"song": "Track B", "artist": "Artist 2", "why": "similar tempo", "sources": []},
        ],
        "codex": [
            {"song": "Track A", "artist": "Artist 1", "why": "matches theme", "sources": ["https://example.com/b"]},
            {"song": "Track C", "artist": "Artist 3", "why": "recommended by critics", "sources": []},
        ],
    }

    combined = synthesize_results(provider_results, limit=10)

    first = combined[0]
    assert first["song"] == "Track A"
    assert "claude" in first["providers"]
    assert "codex" in first["providers"]
    assert len(first["sources"]) == 2


def test_extract_constraints_monthly_listeners():
    constraints = extract_constraints("artists under 50k monthly listeners")
    assert constraints["max_monthly_listeners"] == 50000


def test_requested_metrics_similarity():
    metrics = extract_requested_metrics("songs like Royel Otis with slow bpm")
    assert "similarity" in metrics
    assert "bpm" in metrics
