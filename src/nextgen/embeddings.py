from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

_TQDM_LOCK_MADE_THREAD_SAFE = False


def _ensure_thread_safe_tqdm_lock() -> None:
    """Force tqdm to use a threading lock instead of its default multiprocessing one.

    tqdm's default write-lock is a ``multiprocessing.RLock``. Merely *creating*
    that lock makes CPython spawn the multiprocessing ``resource_tracker`` (via
    ``posix_spawn``). Inside the Textual TUI -- which owns the controlling
    terminal and remaps file descriptors -- that spawn raises
    ``ValueError: bad value(s) in fds_to_keep`` and kills the embed stage.

    We never coordinate progress bars across processes, so a threading lock is
    sufficient and never triggers the spawn. This must run before the first bar
    is constructed (i.e. before any ``encode`` call), hence it is invoked when an
    ``EmbeddingModel`` is created. It is idempotent and process-global.
    """
    global _TQDM_LOCK_MADE_THREAD_SAFE
    if _TQDM_LOCK_MADE_THREAD_SAFE:
        return
    try:
        import threading

        import tqdm

        tqdm.tqdm.set_lock(threading.RLock())
        _TQDM_LOCK_MADE_THREAD_SAFE = True
    except Exception:  # pragma: no cover - tqdm always ships with sentence-transformers
        pass


@dataclass
class EmbeddingModel:
    model_name: str

    def __post_init__(self) -> None:
        # Swap tqdm's multiprocessing lock for a threading one before the model
        # (and its progress bars) ever load -- see _ensure_thread_safe_tqdm_lock.
        _ensure_thread_safe_tqdm_lock()
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # pragma: no cover - import-time environment failure
            raise RuntimeError(_describe_import_failure(exc)) from exc

        self._model = SentenceTransformer(self.model_name)

    def embed(self, texts: Iterable[str]) -> List[List[float]]:
        items = list(texts)
        if not items:
            return []
        # show_progress_bar=False keeps the TUI log clean (no carriage-return
        # bar spam); the crash itself is prevented by the threading lock above.
        vectors = self._model.encode(items, normalize_embeddings=True, show_progress_bar=False)
        return [vec.tolist() for vec in vectors]


def _describe_import_failure(exc: Exception) -> str:
    """Explain a sentence-transformers import failure without misleading the user.

    A blanket "install sentence-transformers" message is wrong when the package
    is already installed but failing to import (e.g. a dependency version skew
    such as ``huggingface_hub`` dropping a symbol). In that case the fix is to
    repair the environment, not to reinstall the package -- so distinguish the
    two cases and surface the real error when the import is merely broken.
    """
    top_level_missing = (
        isinstance(exc, ModuleNotFoundError)
        and (exc.name or "").split(".")[0] == "sentence_transformers"
    )
    if top_level_missing:
        return (
            "sentence-transformers is required for local embeddings. "
            "Install with: pip install sentence-transformers"
        )
    return (
        "sentence-transformers is installed but failed to import "
        f"({type(exc).__name__}: {exc}). This usually means a dependency "
        "version conflict (e.g. an incompatible huggingface_hub or transformers) "
        "in the active Python environment -- repair the environment rather than "
        "reinstalling sentence-transformers."
    )
