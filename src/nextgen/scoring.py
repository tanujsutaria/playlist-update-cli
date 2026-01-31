from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import numpy as np


@dataclass
class ScoreConfig:
    strict_weight: float = 0.4
    base_weight: float = 0.6
    source_weight: float = 0.05
    year_weight: float = 0.05
    year_tolerance: int = 10
    source_cap: int = 5
    year_target: Optional[int] = None


def _cosine_similarity(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return np.array([])
    if vector.size == 0:
        return np.zeros(matrix.shape[0])
    return np.dot(matrix, vector)


def _parse_year(value: object) -> Optional[int]:
    if value is None:
        return None
    text = str(value)
    for token in text.split():
        if len(token) >= 4 and token[:4].isdigit():
            year = int(token[:4])
            if 1900 <= year <= 2100:
                return year
    return None


def _year_similarity(track_year: Optional[int], target_year: Optional[int], tolerance: int) -> float:
    if not track_year or not target_year or tolerance <= 0:
        return 0.0
    diff = abs(track_year - target_year)
    if diff >= tolerance:
        return 0.0
    return max(0.0, 1.0 - (diff / float(tolerance)))


def score_candidates(
    query_vec: List[float],
    track_vecs: Iterable[List[float]],
    strict_ratios: Iterable[float],
    config: ScoreConfig,
    metadata: Optional[List[Dict[str, Any]]] = None,
) -> List[float]:
    matrix = np.array(list(track_vecs), dtype=float)
    query = np.array(query_vec, dtype=float)

    if matrix.size == 0:
        return []

    base_scores = _cosine_similarity(matrix, query)
    scores: List[float] = []
    for idx, (score, ratio) in enumerate(zip(base_scores, strict_ratios)):
        weight = config.base_weight + config.strict_weight * float(ratio)
        boosted = float(score) * weight
        if metadata:
            meta = metadata[idx] if idx < len(metadata) else {}
            sources_count = int(meta.get("sources_count") or 0)
            source_norm = min(sources_count, config.source_cap) / float(max(config.source_cap, 1))
            boosted += config.source_weight * source_norm
            track_year = _parse_year(meta.get("year"))
            year_sim = _year_similarity(track_year, config.year_target, config.year_tolerance)
            boosted += config.year_weight * year_sim
        scores.append(boosted)
    return scores


def rank_scores(scores: List[float]) -> List[int]:
    return list(np.argsort(-np.array(scores)))
