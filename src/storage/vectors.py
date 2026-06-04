from __future__ import annotations

from array import array
from math import sqrt
from typing import Iterable, List, Sequence


def encode_vector(values: Sequence[float]) -> bytes:
    buf = array("f", values)
    return buf.tobytes()


def decode_vector(blob: bytes) -> List[float]:
    buf = array("f")
    buf.frombytes(blob)
    return list(buf)


def vector_norm(values: Iterable[float]) -> float:
    total = 0.0
    for value in values:
        total += float(value) * float(value)
    return sqrt(total)


def normalize_vector(values: Sequence[float]) -> List[float]:
    norm = vector_norm(values)
    if norm == 0.0:
        return [0.0 for _ in values]
    return [float(value) / norm for value in values]


def taste_centroid(vectors: Iterable[Sequence[float]]) -> List[float]:
    """Unit-normalized mean of `vectors` — the canonical "current taste" vector.

    Computes the element-wise mean of the supplied embeddings and re-normalizes
    it to unit length, so that cosine similarity against the centroid matches the
    normalized-embedding assumption the scoring code relies on (the mean of unit
    vectors is generally *not* itself a unit vector). Returns an empty list when
    no vectors are supplied. Raises ValueError on inconsistent dimensions.
    """
    total: List[float] = []
    count = 0
    for vec in vectors:
        if not total:
            total = [0.0] * len(vec)
        elif len(vec) != len(total):
            raise ValueError(
                f"taste_centroid: inconsistent vector dimensions ({len(vec)} != {len(total)})"
            )
        for index, value in enumerate(vec):
            total[index] += float(value)
        count += 1
    if count == 0:
        return []
    mean = [value / count for value in total]
    return normalize_vector(mean)


def mean_vector(vectors: Iterable[Sequence[float]]) -> List[float]:
    """Plain element-wise mean (NOT unit-normalized).

    Unlike `taste_centroid`, this preserves the original scale — for 0–1 feature
    vectors (e.g. sonic features) the mean stays interpretable as "average
    danceability/loudness/…". Returns [] for no input; raises on dim mismatch.
    """
    total: List[float] = []
    count = 0
    for vec in vectors:
        if not total:
            total = [0.0] * len(vec)
        elif len(vec) != len(total):
            raise ValueError(
                f"mean_vector: inconsistent vector dimensions ({len(vec)} != {len(total)})"
            )
        for index, value in enumerate(vec):
            total[index] += float(value)
        count += 1
    if count == 0:
        return []
    return [value / count for value in total]
