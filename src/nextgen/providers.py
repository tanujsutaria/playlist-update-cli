from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from web_search import run_deep_search


@dataclass
class ProviderRun:
    results: List[Dict[str, Any]]
    providers: List[str]
    summary: str
    constraints: Dict[str, Any]
    policy: Dict[str, Any]


def run_providers(query: str, expanded: bool = False) -> ProviderRun:
    results, _, providers, error, _, summary, constraints, policy = run_deep_search(
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
    )
