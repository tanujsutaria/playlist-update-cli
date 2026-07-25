"""Unit tests for src/nextgen/providers.py run_providers.

run_providers delegates to web_search.run_deep_search; we monkeypatch the name
imported into the providers module so no subprocess/search ever runs.
"""

from __future__ import annotations

import pytest

from nextgen import providers
from nextgen.providers import ProviderRun, run_providers


def _canned(
    results=None,
    providers_list=None,
    error=None,
    summary="ok",
    constraints=None,
    policy=None,
    requested_metrics=None,
):
    """Build the 8-tuple shape run_deep_search returns."""
    return (
        results if results is not None else [{"song": "S", "artist": "A"}],
        {},  # per-provider results (ignored by run_providers)
        providers_list if providers_list is not None else ["openai"],
        error,
        requested_metrics if requested_metrics is not None else [],  # index 4 = requested_metrics
        summary,
        constraints if constraints is not None else {"limit": 10},
        policy if policy is not None else {"expanded": False},
    )


class TestRunProviders:
    def test_normalizes_into_provider_run(self, monkeypatch):
        captured = {}

        def fake_run_deep_search(query, expanded=False, on_progress=None):
            captured["query"] = query
            captured["expanded"] = expanded
            return _canned(
                results=[{"song": "Title", "artist": "Band"}],
                providers_list=["openai", "anthropic"],
                summary="found 1",
                constraints={"limit": 5},
                policy={"expanded": True},
                requested_metrics=["similarity", "monthly_listeners"],
            )

        monkeypatch.setattr(providers, "run_deep_search", fake_run_deep_search)

        run = run_providers("indie rock 2020", expanded=True)

        assert isinstance(run, ProviderRun)
        assert run.results == [{"song": "Title", "artist": "Band"}]
        assert run.providers == ["openai", "anthropic"]
        assert run.summary == "found 1"
        assert run.constraints == {"limit": 5}
        assert run.policy == {"expanded": True}
        # Index 4 of the 8-tuple is now threaded through, not discarded.
        assert run.requested_metrics == ["similarity", "monthly_listeners"]
        # Arguments were forwarded.
        assert captured["query"] == "indie rock 2020"
        assert captured["expanded"] is True

    def test_default_expanded_is_false(self, monkeypatch):
        captured = {}

        def fake_run_deep_search(query, expanded=False, on_progress=None):
            captured["expanded"] = expanded
            return _canned()

        monkeypatch.setattr(providers, "run_deep_search", fake_run_deep_search)

        run_providers("a query")
        assert captured["expanded"] is False

    def test_empty_results_yields_empty_provider_run(self, monkeypatch):
        monkeypatch.setattr(
            providers,
            "run_deep_search",
            lambda query, expanded=False, on_progress=None: _canned(
                results=[], providers_list=[], summary="", constraints={}, policy={}
            ),
        )
        run = run_providers("nothing")
        assert run.results == []
        assert run.providers == []
        assert run.summary == ""

    def test_error_raises_runtime_error(self, monkeypatch):
        monkeypatch.setattr(
            providers,
            "run_deep_search",
            lambda query, expanded=False, on_progress=None: _canned(
                error="No search providers configured."
            ),
        )
        with pytest.raises(RuntimeError, match="No search providers configured."):
            run_providers("anything")


class TestRunProvidersOnProgress:
    def test_forwards_on_progress_callback(self, monkeypatch):
        captured = {}

        def fake_run_deep_search(query, expanded=False, on_progress=None):
            captured["on_progress"] = on_progress
            return _canned()

        monkeypatch.setattr(providers, "run_deep_search", fake_run_deep_search)

        def _cb(note: str) -> None:
            pass

        run_providers("a query", on_progress=_cb)
        assert captured["on_progress"] is _cb

    def test_on_progress_defaults_to_none(self, monkeypatch):
        """Backward compat: existing two-arg callers keep working unchanged."""
        captured = {}

        def fake_run_deep_search(query, expanded=False, on_progress=None):
            captured["on_progress"] = on_progress
            return _canned()

        monkeypatch.setattr(providers, "run_deep_search", fake_run_deep_search)

        run_providers("a query")
        assert captured["on_progress"] is None
