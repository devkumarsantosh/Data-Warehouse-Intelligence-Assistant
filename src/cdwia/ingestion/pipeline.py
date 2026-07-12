"""
Async ingestion pipeline (Section 7). Runs entirely outside the request
path so new billing data or documents never block or slow down live
queries. Every chunk is tagged with a schema/document version at
ingestion time, which is what makes it possible to later answer an
audit question like "what did the assistant know when it gave this
recommendation on this date."
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol

logger = logging.getLogger("cdwia.ingestion")


@dataclass(frozen=True)
class RawEvent:
    event_id: str
    event_type: str  # "billing_change" | "document_upsert"
    payload: dict
    schema_version: str
    received_at: datetime


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk_id: str
    source_event_id: str
    text: str
    embedding: list[float]
    schema_version: str
    document_version: str | None
    ingested_at: datetime


class Chunker(Protocol):
    def chunk(self, text: str) -> Iterable[str]: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VectorSink(Protocol):
    def upsert(self, chunks: list[EmbeddedChunk]) -> None: ...


class ObjectStorageSink(Protocol):
    def put(self, key: str, content: bytes) -> None: ...


class CatalogSink(Protocol):
    """Writes lineage/version metadata (e.g. OpenMetadata/DataHub)."""

    def record(self, event: RawEvent, chunk_ids: list[str]) -> None: ...


def _chunk_id(event_id: str, index: int) -> str:
    return hashlib.sha256(f"{event_id}:{index}".encode()).hexdigest()[:16]


class IngestionPipeline:
    def __init__(
        self,
        chunker: Chunker,
        embedder: Embedder,
        vector_sink: VectorSink,
        object_storage_sink: ObjectStorageSink,
        catalog_sink: CatalogSink,
    ):
        self.chunker = chunker
        self.embedder = embedder
        self.vector_sink = vector_sink
        self.object_storage_sink = object_storage_sink
        self.catalog_sink = catalog_sink

    def process_event(self, event: RawEvent) -> list[EmbeddedChunk]:
        raw_text = event.payload.get("text", "")
        # Source-of-truth object storage write happens regardless of
        # chunk/embed outcome, so raw content is never lost even if
        # embedding fails downstream.
        self.object_storage_sink.put(
            key=f"{event.event_type}/{event.event_id}.raw",
            content=raw_text.encode("utf-8"),
        )

        pieces = list(self.chunker.chunk(raw_text))
        if not pieces:
            logger.info("Event %s produced no chunks (empty payload)", event.event_id)
            return []

        vectors = self.embedder.embed(pieces)
        chunks = [
            EmbeddedChunk(
                chunk_id=_chunk_id(event.event_id, i),
                source_event_id=event.event_id,
                text=piece,
                embedding=vec,
                schema_version=event.schema_version,
                document_version=event.payload.get("document_version"),
                ingested_at=datetime.utcnow(),
            )
            for i, (piece, vec) in enumerate(zip(pieces, vectors))
        ]

        self.vector_sink.upsert(chunks)
        self.catalog_sink.record(event, [c.chunk_id for c in chunks])
        logger.info("Ingested event %s into %d chunks", event.event_id, len(chunks))
        return chunks


class SimpleFixedSizeChunker:
    def __init__(self, max_chars: int = 1200, overlap: int = 150):
        self.max_chars = max_chars
        self.overlap = overlap

    def chunk(self, text: str) -> Iterable[str]:
        if not text:
            return []
        step = max(self.max_chars - self.overlap, 1)
        return [text[i : i + self.max_chars] for i in range(0, len(text), step)]
