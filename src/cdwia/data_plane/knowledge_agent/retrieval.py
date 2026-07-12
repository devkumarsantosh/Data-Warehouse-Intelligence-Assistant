"""
Knowledge agent: BM25 keyword search + vector semantic search, fused via
reciprocal rank fusion, then cross-encoder reranked and compressed.

BM25 catches exact terms (SKUs, account IDs, service codes) that a
vector search alone tends to miss; vector search catches semantic
intent that pure keyword matching misses. Neither alone is sufficient.
"""
from __future__ import annotations

from typing import Protocol

from cdwia.common.config import settings
from cdwia.common.models import RetrievedChunk


class KeywordSearchBackend(Protocol):
    def search(self, query: str, top_k: int) -> list[RetrievedChunk]: ...


class VectorSearchBackend(Protocol):
    def search(self, query: str, top_k: int) -> list[RetrievedChunk]: ...


class Reranker(Protocol):
    def rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]: ...


def reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievedChunk]], k: int = 60
) -> list[RetrievedChunk]:
    """Standard RRF: score(d) = sum(1 / (k + rank_i(d))) across each ranked
    list the document appears in. k=60 is the commonly used default that
    dampens the impact of any single list's top position."""
    scores: dict[str, float] = {}
    chunk_by_id: dict[str, RetrievedChunk] = {}
    for ranked in ranked_lists:
        for rank, chunk in enumerate(ranked, start=1):
            scores[chunk.source_id] = scores.get(chunk.source_id, 0.0) + 1.0 / (k + rank)
            chunk_by_id.setdefault(chunk.source_id, chunk)
    fused_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [
        chunk_by_id[cid].model_copy(update={"score": scores[cid]}) for cid in fused_ids
    ]


class KnowledgeAgent:
    def __init__(
        self,
        keyword_backend: KeywordSearchBackend,
        vector_backend: VectorSearchBackend,
        reranker: Reranker,
    ):
        self.keyword_backend = keyword_backend
        self.vector_backend = vector_backend
        self.reranker = reranker

    def retrieve(self, query: str) -> list[RetrievedChunk]:
        keyword_hits = self.keyword_backend.search(query, top_k=settings.bm25_top_k)
        vector_hits = self.vector_backend.search(query, top_k=settings.vector_top_k)
        fused = reciprocal_rank_fusion([keyword_hits, vector_hits])
        # Contextual compression happens inside the reranker: it returns
        # only the top_k most relevant chunks, trimmed to relevant spans,
        # rather than full-document dumps into the synthesizer's context.
        return self.reranker.rerank(query, fused, top_k=settings.rerank_top_k)
