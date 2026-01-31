from __future__ import annotations

from storage.cache import compute_query_hash


def test_query_hash_deterministic() -> None:
    hash_a = compute_query_hash("Dreamy Indie", {"max": 10, "min": 2})
    hash_b = compute_query_hash("  dreamy   indie ", {"min": 2, "max": 10})
    assert hash_a == hash_b
