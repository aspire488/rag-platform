"""Vector store package (plan v2.4 Phase 2): Qdrant local-mode adapter
with contained D: paths and a deliberately narrow, validated interface."""

from __future__ import annotations

from rag_platform.vectorstore.qdrant_local import (
    CollectionError,
    InvalidFilterError,
    InvalidPointError,
    InvalidVectorSizeError,
    PayloadMatch,
    Point,
    QdrantLocalStore,
    SearchHit,
    VectorStoreError,
)

__all__ = [
    "CollectionError",
    "InvalidFilterError",
    "InvalidPointError",
    "InvalidVectorSizeError",
    "PayloadMatch",
    "Point",
    "QdrantLocalStore",
    "SearchHit",
    "VectorStoreError",
]
