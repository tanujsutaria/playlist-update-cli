from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from web_search import run_deep_search


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


def run_providers(query: str, expanded: bool = False) -> ProviderRun:
    results, _, providers, error, requested_metrics, summary, constraints, policy = run_deep_search(
        query=query,
        expanded=expanded,
    )
    if error:
        raise RuntimeError(error)
    return ProviderRun(
        results=results,
        providers=providers,
        summary=summary,
        constraints=constraints,
        policy=policy,
        requested_metrics=requested_metrics,
    )
