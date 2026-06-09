"""Unit tests for src/nextgen/embeddings.py.

EmbeddingModel wraps sentence-transformers, which conftest.py replaces with a
deterministic, offline stub so these tests need no model download.
"""

from __future__ import annotations

import numpy as np

from nextgen.embeddings import EmbeddingModel, _describe_import_failure


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

    def test_constructing_model_makes_tqdm_lock_thread_safe(self):
        """The real crash fix: tqdm's default write-lock is a
        multiprocessing.RLock whose creation spawns the resource_tracker
        (posix_spawn), which dies with "bad value(s) in fds_to_keep" inside the
        Textual TUI. Constructing an EmbeddingModel must swap it for a threading
        lock so that spawn never happens.
        """
        import tqdm

        EmbeddingModel("any")  # triggers _ensure_thread_safe_tqdm_lock()
        lock_module = type(tqdm.tqdm.get_lock()).__module__
        assert "multiprocessing" not in lock_module, (
            f"tqdm lock is still a multiprocessing lock ({lock_module}); "
            "it will spawn the resource_tracker and crash the TUI"
        )

    def test_embed_disables_progress_bar(self, monkeypatch):
        """Regression guard for the Textual-TUI crash.

        sentence-transformers' tqdm progress bar instantiates a
        multiprocessing.RLock, which spawns the multiprocessing resource_tracker
        via posix_spawn. Inside the TUI (which remaps the terminal's file
        descriptors) that spawn dies with "bad value(s) in fds_to_keep" and kills
        the embed stage. embed() must therefore call encode with
        show_progress_bar=False.
        """
        model = EmbeddingModel("any")
        captured: dict = {}

        def spy_encode(items, **kwargs):
            captured.update(kwargs)
            return np.zeros((len(items), 4))

        monkeypatch.setattr(model._model, "encode", spy_encode)
        model.embed(["a", "b"])
        assert captured.get("show_progress_bar") is False


class TestDescribeImportFailure:
    def test_missing_package_says_install(self):
        exc = ModuleNotFoundError(
            "No module named 'sentence_transformers'", name="sentence_transformers"
        )
        msg = _describe_import_failure(exc)
        assert "pip install sentence-transformers" in msg

    def test_broken_install_surfaces_real_error_not_install_hint(self):
        # The exact failure that bit a real environment: ST is installed but its
        # import chain is broken by a huggingface_hub version skew.
        exc = ImportError("cannot import name 'cached_download' from 'huggingface_hub'")
        msg = _describe_import_failure(exc)
        assert "failed to import" in msg
        assert "cached_download" in msg
        # Must NOT tell the user to reinstall an already-installed package.
        assert "pip install" not in msg

    def test_missing_transitive_dependency_is_not_treated_as_absent(self):
        # A missing *dependency* of an installed ST should surface, not be
        # mislabeled as "sentence-transformers not installed".
        exc = ModuleNotFoundError("No module named 'huggingface_hub'", name="huggingface_hub")
        msg = _describe_import_failure(exc)
        assert "pip install sentence-transformers" not in msg
        assert "failed to import" in msg
