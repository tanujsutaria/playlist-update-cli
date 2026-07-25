from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from web_search import run_deep_search

# The exact sentinel run_deep_search returns when detect_search_commands()
# finds no provider at all (no API keys, no WEB_SEARCH_* overrides).
_NO_PROVIDERS_ERROR = "No search providers configured."


class ProviderConfigError(RuntimeError):
    """No search provider is configured (vs. a provider that ran and failed).

    Subclasses RuntimeError so existing `except RuntimeError` callers keep
    working; the UI catches this subtype to render an actionable fix.
    """


@dataclass
class ProviderRun:
    results: List[Dict[str, Any]]
    providers: List[str]
    summary: str
    constraints: Dict[str, Any]
    policy: Dict[str, Any]
    # The metrics the query asked to be surfaced (e.g. "monthly_listeners",
    # "similarity"). run_deep_search returns this as 8-tuple index 4; it used to
    # be discarded here. Defaulted + last so existing ProviderRun(...) callers
    # (tests/test_enrich.py, tests/bdd/test_search.py) stay valid.
    requested_metrics: List[str] = field(default_factory=list)


def run_providers(
    query: str,
    expanded: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
) -> ProviderRun:
    results, _, providers, error, requested_metrics, summary, constraints, policy = run_deep_search(
        query=query,
        expanded=expanded,
        on_progress=on_progress,
    )
    if error:
        if error == _NO_PROVIDERS_ERROR:
            raise ProviderConfigError(error)
        raise RuntimeError(error)
    return ProviderRun(
        results=results,
        providers=providers,
        summary=summary,
        constraints=constraints,
        policy=policy,
        requested_metrics=requested_metrics,
    )
