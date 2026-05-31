"""Unit tests for src/storage/vectors.py."""

from __future__ import annotations

import math

import pytest

from storage.vectors import (
    decode_vector,
    encode_vector,
    normalize_vector,
    taste_centroid,
    vector_norm,
)


class TestEncodeDecodeRoundTrip:
    def test_round_trip_preserves_values_within_float32_tolerance(self):
        original = [0.1, -2.5, 3.14159, 100.0, 0.0]
        blob = encode_vector(original)
        decoded = decode_vector(blob)
        assert len(decoded) == len(original)
        for o, d in zip(original, decoded):
            assert d == pytest.approx(o, rel=1e-6, abs=1e-6)

    def test_encode_returns_bytes(self):
        blob = encode_vector([1.0, 2.0])
        assert isinstance(blob, bytes)
        # float32 -> 4 bytes per element
        assert len(blob) == 8

    def test_empty_vector_round_trip(self):
        assert decode_vector(encode_vector([])) == []

    def test_single_element(self):
        decoded = decode_vector(encode_vector([42.5]))
        assert decoded == pytest.approx([42.5])


class TestVectorNorm:
    def test_known_norm(self):
        assert vector_norm([3.0, 4.0]) == pytest.approx(5.0)

    def test_zero_vector(self):
        assert vector_norm([0.0, 0.0, 0.0]) == 0.0

    def test_unit_axis(self):
        assert vector_norm([1.0, 0.0, 0.0]) == pytest.approx(1.0)

    def test_matches_math_sqrt_of_sum_of_squares(self):
        values = [1.5, -2.0, 0.5]
        expected = math.sqrt(sum(v * v for v in values))
        assert vector_norm(values) == pytest.approx(expected)


class TestNormalizeVector:
    def test_unit_length_after_normalization(self):
        normalized = normalize_vector([3.0, 4.0])
        assert vector_norm(normalized) == pytest.approx(1.0)

    def test_direction_preserved(self):
        normalized = normalize_vector([3.0, 4.0])
        assert normalized == pytest.approx([0.6, 0.8])

    def test_zero_vector_returns_zeros(self):
        assert normalize_vector([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]

    def test_already_unit_vector(self):
        normalized = normalize_vector([1.0, 0.0])
        assert normalized == pytest.approx([1.0, 0.0])


class TestTasteCentroid:
    def test_empty_returns_empty_list(self):
        assert taste_centroid([]) == []

    def test_single_vector_is_its_normalization(self):
        assert taste_centroid([[3.0, 4.0]]) == pytest.approx([0.6, 0.8])

    def test_result_is_unit_length(self):
        centroid = taste_centroid([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        assert vector_norm(centroid) == pytest.approx(1.0)

    def test_mean_then_normalize_direction(self):
        # mean of (1,0) and (0,1) is (0.5, 0.5) -> normalized to (~0.707, ~0.707).
        centroid = taste_centroid([[1.0, 0.0], [0.0, 1.0]])
        assert centroid == pytest.approx([0.70710678, 0.70710678])

    def test_canceling_vectors_return_zeros(self):
        # mean of (1,0) and (-1,0) is (0,0): normalize must not divide by zero.
        assert taste_centroid([[1.0, 0.0], [-1.0, 0.0]]) == [0.0, 0.0]

    def test_dimension_mismatch_raises(self):
        with pytest.raises(ValueError):
            taste_centroid([[1.0, 2.0], [1.0, 2.0, 3.0]])

    def test_accepts_generator_of_vectors(self):
        centroid = taste_centroid(vec for vec in ([3.0, 4.0],))
        assert centroid == pytest.approx([0.6, 0.8])
