"""Vector store for tender document chunks.

``TenderVectorStore`` prefers chroma (``PersistentClient`` at ``persist_dir``,
one collection ``"tender_chunks"``, metadata ``{tender_id, section, page,
chunk_index}``, embeddings precomputed at upsert time). If chromadb cannot
be imported — or the embedder itself cannot be constructed — it degrades to
a fully in-memory store (``tender_id -> [chunks]``) scored by keyword
overlap. The public API is identical either way:

- ``upsert(tender_id, chunks)`` — replace all chunks for the tender.
- ``query(tender_id, query_text, k=6)`` — top-k chunks for that tender only,
  ``[{"section", "page", "text", "score"}]`` sorted by score desc.
- ``delete_tender(tender_id)`` — drop everything stored for the tender.

Never invents content: scores only reflect stored chunks.
"""

from __future__ import annotations

import re
from collections import Counter

_WORD_RE = re.compile(r"[a-z0-9]{3,}")

# Very small stopword set for the keyword-overlap fallback scorer.
_STOPWORDS = frozenset(
    "the and for with are was were has have had this that these those from "
    "into shall will may must not per all any each other than then also such "
    "under over between within after before during through its their our your "
    "his her who which what when where how why but can should would could bid "
    "bids tender tenders".split()
)


def _keywords(text: str) -> Counter:
    return Counter(
        w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS
    )


class TenderVectorStore:
    def __init__(self, persist_dir: str):
        self.persist_dir = persist_dir
        self._backend = "memory"
        self._mem: dict = {}
        self._embedder = None
        self._collection = None
        try:
            import chromadb  # noqa: F401  (lazy: heavy optional dep)

            from .embeddings import get_embedder

            embedder = get_embedder()
            # NOTE: do not probe-embed here. The TF-IDF fallback fits its
            # vectorizer on the first corpus it sees; a probe call would
            # fit it on a useless single word and poison every later
            # embedding. First real use is the first upsert.
            client = chromadb.PersistentClient(path=persist_dir)
            self._collection = client.get_or_create_collection(
                "tender_chunks", metadata={"hnsw:space": "cosine"}
            )
            self._embedder = embedder
            self._backend = "chroma"
        except Exception:
            # In-memory fallback; scores via keyword overlap.
            self._backend = "memory"
            self._embedder = None
            self._collection = None

    # ------------------------------------------------------------------
    @property
    def backend(self) -> str:
        """'chroma' or 'memory' — useful for diagnostics/tests."""
        return self._backend

    def upsert(self, tender_id: int, chunks: list) -> int:
        """Replace all stored chunks for ``tender_id``. Returns chunk count."""
        chunks = list(chunks or [])
        if self._backend == "chroma":
            self._collection.delete(where={"tender_id": int(tender_id)})
            if chunks:
                texts = [c.get("text") or "" for c in chunks]
                embeddings = self._embedder.embed(texts)
                self._collection.add(
                    ids=[f"{tender_id}-{i}" for i in range(len(chunks))],
                    documents=texts,
                    metadatas=[
                        {
                            "tender_id": int(tender_id),
                            "section": str(c.get("section") or ""),
                            "page": int(c.get("page") or 0),
                            "chunk_index": int(c.get("chunk_index") or 0),
                        }
                        for c in chunks
                    ],
                    embeddings=embeddings,
                )
        else:
            self._mem[int(tender_id)] = chunks
        return len(chunks)

    def query(self, tender_id: int, query_text: str, k: int = 6) -> list:
        """Top-``k`` chunks for ``tender_id`` matching ``query_text``."""
        if not query_text:
            return []
        if self._backend == "chroma":
            qvec = self._embedder.embed([query_text])[0]
            res = self._collection.query(
                query_embeddings=[qvec],
                where={"tender_id": int(tender_id)},
                n_results=max(1, int(k)),
                include=["documents", "metadatas", "distances"],
            )
            out = []
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            for doc, meta, dist in zip(docs, metas, dists):
                # hnsw:space=cosine -> distance = 1 - cosine similarity
                score = max(0.0, min(1.0, 1.0 - float(dist)))
                out.append({
                    "section": (meta or {}).get("section", ""),
                    "page": (meta or {}).get("page", 0),
                    "text": doc or "",
                    "score": score,
                })
            return out

        # In-memory keyword-overlap fallback.
        q_counts = _keywords(query_text)
        if not q_counts:
            return []
        q_total = sum(q_counts.values())
        scored = []
        for c in self._mem.get(int(tender_id), []):
            c_counts = _keywords(c.get("text") or "")
            overlap = sum(min(q_counts[w], c_counts[w]) for w in q_counts if w in c_counts)
            if overlap > 0:
                scored.append({
                    "section": c.get("section", ""),
                    "page": c.get("page", 0),
                    "text": c.get("text") or "",
                    "score": overlap / q_total,
                })
        scored.sort(key=lambda d: d["score"], reverse=True)
        return scored[: max(1, int(k))]

    def delete_tender(self, tender_id: int) -> None:
        """Remove all stored chunks for ``tender_id``."""
        if self._backend == "chroma":
            self._collection.delete(where={"tender_id": int(tender_id)})
        else:
            self._mem.pop(int(tender_id), None)
