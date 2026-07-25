import os
import shutil

from web_search import (
    _extract_output,
    _run_command,
    detect_search_commands,
    extract_constraints,
    extract_requested_metrics,
    synthesize_results,
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
            {
                "song": "Track A",
                "artist": "Artist 1",
                "why": "fits mood",
                "sources": ["https://example.com/a"],
            },
            {"song": "Track B", "artist": "Artist 2", "why": "similar tempo", "sources": []},
        ],
        "codex": [
            {
                "song": "Track A",
                "artist": "Artist 1",
                "why": "matches theme",
                "sources": ["https://example.com/b"],
            },
            {
                "song": "Track C",
                "artist": "Artist 3",
                "why": "recommended by critics",
                "sources": [],
            },
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
                        {
                            "url": "https://example.com/a",
                            "title": "Source A",
                            "snippet": "Snippet A",
                        }
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


def test_claude_wrapper_no_cli_fallback_by_default(monkeypatch):
    """With WEB_SEARCH_CLAUDE_FALLBACK_CLI unset, an empty wrapper result must NOT
    retry the broken `claude --json` CLI — the fallback now defaults off."""
    if shutil.which("claude") is None:
        return

    empty_wrapper_output = '{"summary":"","results":[]}'

    class DummyResult:
        def __init__(self, stdout: str):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    calls = {"count": 0}

    def fake_run(args, input=None, text=None, capture_output=None, timeout=None, env=None):
        calls["count"] += 1
        return DummyResult(empty_wrapper_output)

    monkeypatch.setattr("web_search.subprocess.run", fake_run)
    monkeypatch.delenv("WEB_SEARCH_CLAUDE_FALLBACK_CLI", raising=False)
    monkeypatch.setattr("web_search._is_anthropic_wrapper_command", lambda command: True)

    results, summary = _run_command(
        "claude",
        "python -m src.anthropic_web_search_wrapper",
        {"query": "x"},
        10,
    )

    assert results == []
    assert calls["count"] == 1  # only the wrapper ran; no CLI fallback


def test_run_command_nonexistent_executable_fails_gracefully(monkeypatch):
    # A command whose executable does not resolve must fail gracefully (return
    # the empty result, not raise) and must never spawn a subprocess.
    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called for a missing executable")

    monkeypatch.setattr("web_search.subprocess.run", fail_run)

    results, summary = _run_command(
        "web",
        "definitely-not-a-real-binary-xyz --json",
        {"query": "x"},
        10,
    )

    assert results == []
    assert summary == ""


def test_run_command_retry_depth_is_bounded(monkeypatch):
    # Force the codex "unexpected argument" retry condition on every call and
    # confirm the recursion stops at the depth cap instead of looping forever.
    # The reported flag is NOT present in argv, so _strip_flag is a no-op and
    # the same argv recurses each time, exercising the depth guard.
    from web_search import _MAX_RUN_DEPTH

    calls = {"count": 0}

    class DummyResult:
        returncode = 1
        stdout = ""
        stderr = "error: unexpected argument '--bogus' found"

    def fake_run(*args, **kwargs):
        calls["count"] += 1
        return DummyResult()

    monkeypatch.setattr("web_search.subprocess.run", fake_run)
    # Make the executable resolve so we reach subprocess.run on each attempt.
    monkeypatch.setattr("web_search.shutil.which", lambda name: "/usr/bin/codex")

    results, summary = _run_command("codex", "codex exec -", {"query": "x"}, 10)

    assert results == []
    assert summary == ""
    # Initial call + at most _MAX_RUN_DEPTH retries before the guard trips.
    assert calls["count"] <= _MAX_RUN_DEPTH + 1


# ---------------------------------------------------------------------------
# run_deep_search on_progress: "providers k/N" liveness, fired per completed
# run (success OR failure), optional and fully backward-compatible.
# ---------------------------------------------------------------------------


def _patch_single_provider(monkeypatch, parallel=2, fail=False):
    """One fake provider, `parallel` runs, no subprocess/network."""
    import web_search

    monkeypatch.setenv("WEB_SEARCH_PARALLEL_PER_PROVIDER", str(parallel))
    monkeypatch.delenv("WEB_SEARCH_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("WEB_SEARCH_MODEL", raising=False)
    monkeypatch.setattr(
        web_search, "detect_search_commands", lambda env=None: {"claude": "claude --json"}
    )

    def _fake_run(label, command, payload, timeout_sec):
        if fail:
            raise RuntimeError("boom")
        return ([{"song": "S", "artist": "A", "sources": []}], "summary")

    monkeypatch.setattr(web_search, "_run_command", _fake_run)


def test_run_deep_search_emits_providers_progress(monkeypatch):
    from web_search import run_deep_search

    _patch_single_provider(monkeypatch, parallel=2)
    seen = []

    results, _, providers, error, _, _, _, _ = run_deep_search(
        "some query", on_progress=seen.append
    )

    assert error is None
    assert results
    assert providers == ["claude"]
    # Numbered by completion order, so the sequence is deterministic even
    # though the two runs race on real threads.
    assert seen == ["providers 1/2", "providers 2/2"]


def test_run_deep_search_without_on_progress_is_backward_compatible(monkeypatch):
    from web_search import run_deep_search

    _patch_single_provider(monkeypatch, parallel=1)

    results, _, _, error, _, _, _, _ = run_deep_search("some query")

    assert error is None
    assert results


def test_run_deep_search_counts_failed_runs(monkeypatch):
    """A run that raises still advances the counter — it tracks liveness, not success."""
    from web_search import run_deep_search

    _patch_single_provider(monkeypatch, parallel=1, fail=True)
    seen = []

    results, _, _, error, _, _, _, _ = run_deep_search("some query", on_progress=seen.append)

    assert seen == ["providers 1/1"]
    assert results == []
    assert error == "No results returned by providers."


def test_run_deep_search_survives_progress_callback_errors(monkeypatch):
    from web_search import run_deep_search

    _patch_single_provider(monkeypatch, parallel=1)

    def _explode(note):
        raise RuntimeError("callback bug")

    results, _, _, error, _, _, _, _ = run_deep_search("some query", on_progress=_explode)

    assert error is None
    assert results
