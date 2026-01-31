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
