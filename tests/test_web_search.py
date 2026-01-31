import os
import shutil

from web_search import (
    detect_search_commands,
    synthesize_results,
    extract_constraints,
    extract_requested_metrics,
    _extract_output,
    _run_command,
)


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


def test_synthesize_results_merges_source_details():
    provider_results = {
        "claude": [
            {
                "song": "Track A",
                "artist": "Artist 1",
                "sources": ["https://example.com/a"],
                "source_details": [{"url": "https://example.com/a", "title": "Source A"}],
            }
        ],
        "codex": [
            {
                "song": "Track A",
                "artist": "Artist 1",
                "sources": ["https://example.com/b"],
                "source_details": [{"url": "https://example.com/b", "snippet": "Snippet B"}],
            }
        ],
    }

    combined = synthesize_results(provider_results, limit=5)
    details = combined[0]["source_details"]

    urls = {detail.get("url") for detail in details}
    assert "https://example.com/a" in urls
    assert "https://example.com/b" in urls


def test_extract_constraints_monthly_listeners():
    constraints = extract_constraints("artists under 50k monthly listeners")
    assert constraints["max_monthly_listeners"] == 50000


def test_requested_metrics_similarity():
    metrics = extract_requested_metrics("songs like Royel Otis with slow bpm")
    assert "similarity" in metrics
    assert "bpm" in metrics


def test_extract_output_from_claude_json_response():
    output = {
        "type": "message",
        "content": [
            {
                "type": "text",
                "text": (
                    '{"summary":"ok","results":[{"song":"Track A","artist":"Artist 1",'
                    '"sources":[],"metrics":{}}]}'
                ),
            }
        ],
    }

    results, summary = _extract_output(output)

    assert summary == "ok"
    assert len(results) == 1
    assert results[0]["song"] == "Track A"


def test_normalize_item_with_artists_list():
    results, _ = _extract_output(
        {
            "summary": "ok",
            "results": [
                {
                    "title": "Track A",
                    "artists": ["Artist 1"],
                    "sources": [],
                    "metrics": {},
                }
            ],
        }
    )

    assert len(results) == 1
    assert results[0]["song"] == "Track A"
    assert results[0]["artist"] == "Artist 1"


def test_normalize_item_with_source_details():
    results, _ = _extract_output(
        {
            "summary": "ok",
            "results": [
                {
                    "song": "Track A",
                    "artist": "Artist 1",
                    "sources": [
                        {"url": "https://example.com/a", "title": "Source A", "snippet": "Snippet A"}
                    ],
                    "metrics": {},
                }
            ],
        }
    )

    assert results[0]["sources"] == ["https://example.com/a"]
    assert results[0]["source_details"][0]["title"] == "Source A"


def test_claude_wrapper_fallbacks_to_cli(monkeypatch):
    if shutil.which("claude") is None:
        return

    empty_wrapper_output = '{"summary":"","results":[]}'
    cli_output = '{"summary":"ok","results":[{"song":"Track A","artist":"Artist 1","sources":[],"metrics":{}}]}'

    class DummyResult:
        def __init__(self, stdout: str):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    calls = {"count": 0}

    def fake_run(args, input=None, text=None, capture_output=None, timeout=None, env=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return DummyResult(empty_wrapper_output)
        return DummyResult(cli_output)

    monkeypatch.setattr("web_search.subprocess.run", fake_run)
    monkeypatch.setenv("WEB_SEARCH_CLAUDE_FALLBACK_CLI", "1")
    monkeypatch.setattr("web_search._is_anthropic_wrapper_command", lambda command: True)

    results, summary = _run_command(
        "claude",
        "python -m src.anthropic_web_search_wrapper",
        {"query": "x"},
        10,
    )

    assert summary == "ok"
    assert len(results) == 1
