"""Unit tests for src/nextgen/embeddings.py.

EmbeddingModel wraps sentence-transformers, which conftest.py replaces with a
deterministic, offline stub so these tests need no model download.
"""

from __future__ import annotations

from nextgen.embeddings import EmbeddingModel


class TestEmbeddingModel:
    def test_instantiates_with_any_model_name(self):
        model = EmbeddingModel("any-model-name")
        assert model.model_name == "any-model-name"

    def test_embed_returns_list_of_float_lists(self):
        model = EmbeddingModel("any")
        vectors = model.embed(["a", "b"])
        assert isinstance(vectors, list)
        assert len(vectors) == 2
        for vec in vectors:
            assert isinstance(vec, list)
            assert all(isinstance(v, float) for v in vec)

    def test_embed_vectors_have_consistent_length(self):
        model = EmbeddingModel("any")
        vectors = model.embed(["one", "two", "three"])
        lengths = {len(v) for v in vectors}
        assert len(lengths) == 1
        assert lengths.pop() > 0

    def test_embed_is_deterministic(self):
        model = EmbeddingModel("any")
        first = model.embed(["repeatable"])
        second = model.embed(["repeatable"])
        assert first == second

    def test_embed_empty_returns_empty_list(self):
        model = EmbeddingModel("any")
        assert model.embed([]) == []

    def test_embed_accepts_arbitrary_iterable(self):
        model = EmbeddingModel("any")
        vectors = model.embed(iter(["x", "y"]))
        assert len(vectors) == 2
