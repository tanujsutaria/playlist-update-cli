from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

import numpy as np


@dataclass
class ScoreConfig:
    strict_weight: float = 0.4
    base_weight: float = 0.6


def _cosine_similarity(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return np.array([])
    if vector.size == 0:
        return np.zeros(matrix.shape[0])
    return np.dot(matrix, vector)


def score_candidates(
    query_vec: List[float],
    track_vecs: Iterable[List[float]],
    strict_ratios: Iterable[float],
    config: ScoreConfig,
) -> List[float]:
    matrix = np.array(list(track_vecs), dtype=float)
    query = np.array(query_vec, dtype=float)

    if matrix.size == 0:
        return []

    base_scores = _cosine_similarity(matrix, query)
    scores: List[float] = []
    for score, ratio in zip(base_scores, strict_ratios):
        weight = config.base_weight + config.strict_weight * float(ratio)
        scores.append(float(score) * weight)
    return scores


def rank_scores(scores: List[float]) -> List[int]:
    return list(np.argsort(-np.array(scores)))
