from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


@dataclass
class EmbeddingModel:
    model_name: str

    def __post_init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "sentence-transformers is required for local embeddings. "
                "Install with: pip install sentence-transformers"
            ) from exc

        self._model = SentenceTransformer(self.model_name)

    def embed(self, texts: Iterable[str]) -> List[List[float]]:
        items = list(texts)
        if not items:
            return []
        vectors = self._model.encode(items, normalize_embeddings=True)
        return [vec.tolist() for vec in vectors]
