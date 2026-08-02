"""Centralized environment-variable coercion and application configuration.

This module provides robust ``env_*`` helpers (empty/invalid values fall back
to the supplied default) and a frozen :class:`AppConfig` dataclass that captures
the search/embedding settings read from the environment. Keeping the coercion
logic in one place avoids the duplicated nested helpers that previously lived in
``main.py``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def project_root() -> Path:
    """Repository root — the directory containing ``src/``, ``data/``, ``backups/``.

    The single seam for anchoring state directories: callers use
    ``config.project_root()`` (module-qualified) so tests can monkeypatch this
    one function instead of intercepting ``Path`` in whichever module the
    caller currently lives.
    """
    return Path(__file__).resolve().parent.parent


def env_float(name: str, default: float, env: Optional[Mapping[str, str]] = None) -> float:
    """Read ``name`` from the environment as a float.

    Empty, unset, or non-numeric values fall back to ``default``.
    """

    source = env if env is not None else os.environ
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using %s.", name, raw, default)
        return default


def env_int(name: str, default: int, env: Optional[Mapping[str, str]] = None) -> int:
    """Read ``name`` from the environment as an int.

    Empty, unset, or non-integer values fall back to ``default``.
    """

    source = env if env is not None else os.environ
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using %s.", name, raw, default)
        return default


def env_flag(name: str, default: bool = False, env: Optional[Mapping[str, str]] = None) -> bool:
    """Read ``name`` from the environment as a boolean flag.

    Returns ``True`` for the truthy tokens ``{"1", "true", "yes", "on"}``
    (case-insensitive). Unset or empty values fall back to ``default``;
    any other value is treated as ``False``.
    """

    source = env if env is not None else os.environ
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in _TRUTHY


@dataclass(frozen=True)
class AppConfig:
    """Search/embedding configuration resolved from environment variables.

    The defaults here mirror the historical inline ``os.getenv`` defaults in
    ``main.py`` so that :meth:`from_env` reconstructs the exact same
    ``SearchScoreConfig`` the pipeline used before centralization.
    """

    model_name: str = "all-mpnet-base-v2"
    strict_threshold: float = 0.6
    lenient_threshold: float = 0.75
    strict_weight: float = 0.4
    base_weight: float = 0.6
    source_weight: float = 0.05
    year_weight: float = 0.05
    year_tolerance: int = 10
    source_cap: int = 5

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AppConfig":
        """Build an :class:`AppConfig` from the environment via the ``env_*`` helpers."""

        source = env if env is not None else os.environ
        return cls(
            model_name=source.get("SEARCH_EMBEDDING_MODEL", "all-mpnet-base-v2"),
            strict_threshold=env_float("SEARCH_STRICT_THRESHOLD", 0.6, source),
            lenient_threshold=env_float("SEARCH_LENIENT_THRESHOLD", 0.75, source),
            strict_weight=env_float("SEARCH_SCORE_STRICT_WEIGHT", 0.4, source),
            base_weight=env_float("SEARCH_SCORE_BASE_WEIGHT", 0.6, source),
            source_weight=env_float("SEARCH_SCORE_SOURCE_WEIGHT", 0.05, source),
            year_weight=env_float("SEARCH_SCORE_YEAR_WEIGHT", 0.05, source),
            year_tolerance=env_int("SEARCH_SCORE_YEAR_TOLERANCE", 10, source),
            source_cap=env_int("SEARCH_SCORE_SOURCE_CAP", 5, source),
        )
