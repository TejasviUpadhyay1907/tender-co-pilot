"""Embedding backends for the tender RAG pipeline.

``get_embedder()`` prefers sentence-transformers (model name from settings,
lazy import). On ANY failure — missing package, missing model, no network
to download it, no API-side config — it falls back to ``TfidfEmbedder``
(sklearn ``TfidfVectorizer`` fitted on the first corpus it sees). Both
expose ``embed(texts) -> list[list[float]]``. sklearn itself is imported
lazily so this module imports even without sklearn installed (a clear
``RuntimeError`` is raised only when the fallback is actually needed).

No app-internal imports happen at module top level.
"""

from __future__ import annotations

import math


def cosine_sim(a: list, b: list) -> float:
    """Cosine similarity between two dense vectors (0.0 for zero vectors)."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class TfidfEmbedder:
    """Fallback embedder: TF-IDF over the first corpus it embeds.

    The vectorizer is fitted once (on the first ``embed`` call) and reused
    for all later calls so embeddings stay in the same space. For the
    retrieval workload here (tens of chunks per tender) this is adequate
    and fully offline.
    """

    def __init__(self, max_features: int = 4096):
        self.max_features = max_features
        self._vectorizer = None

    def _ensure_vectorizer(self):
        if self._vectorizer is not None:
            return self._vectorizer
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
        except Exception as exc:  # pragma: no cover - env issue
            raise RuntimeError(
                "TfidfEmbedder needs scikit-learn installed"
            ) from exc
        self._vectorizer = TfidfVectorizer(
            max_features=self.max_features, ngram_range=(1, 2)
        )
        return self._vectorizer

    def fit(self, corpus: list) -> "TfidfEmbedder":
        """Explicitly fit the vectorizer on a corpus of documents."""
        self._ensure_vectorizer().fit(list(corpus) or [""])
        return self

    def embed(self, texts: list) -> list:
        """Embed texts; fits on the input on first use."""
        texts = list(texts)
        if not texts:
            return []
        vec = self._ensure_vectorizer()
        if self._vectorizer is None or not hasattr(vec, "vocabulary_"):
            # not fitted yet: fit on this batch
            self.fit(texts)
        return vec.transform(texts).toarray().tolist()


class _SentenceTransformerEmbedder:
    """Thin wrapper around a sentence-transformers model."""

    def __init__(self, model):
        self._model = model

    def embed(self, texts: list) -> list:
        texts = list(texts)
        if not texts:
            return []
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]


def get_embedder():
    """Return a sentence-transformers embedder, or the TF-IDF fallback.

    The model name comes from ``settings.EMBEDDINGS_MODEL`` (lazy import of
    ``app.config``). Any failure — no settings, no package, model download
    blocked — returns ``TfidfEmbedder``.
    """
    try:
        from app.config import settings  # lazy: config owned by another stream

        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(settings.EMBEDDINGS_MODEL)
        return _SentenceTransformerEmbedder(model)
    except Exception:
        return TfidfEmbedder()
