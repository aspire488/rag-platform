"""Phase 5 dense retrieval tests: deterministic fake embedders over the
Phase 2 local store - similarity ordering, total reads (missing
collection), provenance payload requirements, and honest error contracts.

No model is ever constructed (fakes only), no network, no services.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from rag_platform.retrieval.dense import dense_search
from rag_platform.retrieval.errors import DenseIndexError
from rag_platform.vectorstore.qdrant_local import (
    Point,
    QdrantLocalStore,
    VectorStoreError,
)


class ScriptedEmbedder:
    """Returns the preset vector for the (single) query text."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]

    @property
    def is_loaded(self) -> bool:
        return True


class WrongShapeEmbedder:
    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0], [0.0, 1.0]]  # 2 vectors for 1 text

    @property
    def is_loaded(self) -> bool:
        return True


class FailingEmbedder:
    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        from rag_platform.embeddings.minilm import EmbeddingError

        raise EmbeddingError("deterministic fake failure (no model involved)")

    @property
    def is_loaded(self) -> bool:
        return False


@pytest.fixture
def store(storage_tmp: Path) -> Iterator[QdrantLocalStore]:
    local = QdrantLocalStore(storage_tmp / "vectors", vector_size=2)
    try:
        yield local
    finally:
        local.close()


def _seed(store: QdrantLocalStore) -> None:
    store.ensure_collection("c")
    store.add(
        "c",
        [
            Point(
                id="00000000-0000-5000-8000-000000000001",
                vector=[1.0, 0.0],
                payload={"chunk_id": "near", "doc_id": "doc-near"},
            ),
            Point(
                id="00000000-0000-5000-8000-000000000002",
                vector=[0.8, 0.6],
                payload={"chunk_id": "mid", "doc_id": "doc-mid"},
            ),
            Point(
                id="00000000-0000-5000-8000-000000000003",
                vector=[0.0, 1.0],
                payload={"chunk_id": "far", "doc_id": "doc-far"},
            ),
        ],
    )


def test_dense_orders_by_descending_cosine_similarity(store: QdrantLocalStore) -> None:
    _seed(store)
    hits = dense_search(store, "c", ScriptedEmbedder([1.0, 0.0]), "any query", limit=10)
    assert [h.chunk_id for h in hits] == ["near", "mid", "far"]
    assert [h.rank for h in hits] == [1, 2, 3]
    assert hits[0].cosine_similarity == pytest.approx(1.0)
    assert hits[1].cosine_similarity == pytest.approx(0.8)
    assert hits[2].cosine_similarity == pytest.approx(0.0)


def test_missing_collection_is_a_total_read(store: QdrantLocalStore) -> None:
    assert dense_search(store, "c", ScriptedEmbedder([1.0, 0.0]), "q", limit=5) == []


def test_unprovenanced_hits_are_skipped_and_ranks_renumbered(
    store: QdrantLocalStore,
) -> None:
    store.ensure_collection("c")
    store.add(
        "c",
        [
            Point(
                id="00000000-0000-5000-8000-000000000011", vector=[1.0, 0.0], payload={}
            ),  # no provenance - cannot be cited
            Point(
                id="00000000-0000-5000-8000-000000000012",
                vector=[0.9, 0.1],
                payload={"chunk_id": "only-chunk"},
            ),  # missing doc_id
            Point(
                id="00000000-0000-5000-8000-000000000013",
                vector=[0.8, 0.2],
                payload={"chunk_id": "good", "doc_id": "doc-good"},
            ),
        ],
    )
    hits = dense_search(store, "c", ScriptedEmbedder([1.0, 0.0]), "q", limit=10)
    assert [h.chunk_id for h in hits] == ["good"]
    assert hits[0].rank == 1  # renumbered over usable hits


def test_wrong_vector_count_raises_dense_index_error(store: QdrantLocalStore) -> None:
    _seed(store)
    with pytest.raises(DenseIndexError):
        dense_search(store, "c", WrongShapeEmbedder(), "q", limit=5)


def test_embedder_errors_propagate_untouched(store: QdrantLocalStore) -> None:
    _seed(store)
    from rag_platform.embeddings.minilm import EmbeddingError

    with pytest.raises(EmbeddingError):
        dense_search(store, "c", FailingEmbedder(), "q", limit=5)


# --- score-space verification (remediation F1) -------------------------
# A cosine score floor must only ever be compared against scores from a
# verified Cosine collection; these pin the read-only metric inspection
# the retriever relies on.


def test_collection_distance_is_none_for_a_missing_collection(
    store: QdrantLocalStore,
) -> None:
    assert store.collection_distance("c") is None  # reads are total


def test_collection_distance_reports_cosine_by_default(
    store: QdrantLocalStore,
) -> None:
    store.ensure_collection("c")
    assert store.collection_distance("c") == "Cosine"


def test_collection_distance_reports_the_actual_non_cosine_metric(
    storage_tmp: Path,
) -> None:
    with QdrantLocalStore(
        storage_tmp / "dot-vectors", vector_size=2, distance="Dot"
    ) as dot:
        dot.ensure_collection("c")
        assert dot.collection_distance("c") == "Dot"
    with QdrantLocalStore(
        storage_tmp / "euclid-vectors", vector_size=2, distance="Euclid"
    ) as euclid:
        euclid.ensure_collection("c")
        assert euclid.collection_distance("c") == "Euclid"


def test_collection_distance_after_close_is_typed(store: QdrantLocalStore) -> None:
    store.close()
    with pytest.raises(VectorStoreError):
        store.collection_distance("c")
