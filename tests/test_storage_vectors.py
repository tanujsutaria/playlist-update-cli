"""Unit tests for src/storage/vectors.py."""

from __future__ import annotations

import math

import pytest

from storage.vectors import decode_vector, encode_vector, normalize_vector, vector_norm


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
