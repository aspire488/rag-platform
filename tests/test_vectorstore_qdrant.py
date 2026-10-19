"""Phase 2 Qdrant local-mode adapter tests (plan v2.4 section 15 Phase 2;
section 12 "Qdrant local persistence + payload filter honored" row).

Deterministic, offline, tiny fixtures: a 4-dimensional vector space and a
handful of points, no embedding model, no downloaded corpora, no network
(the adapter is path-based local mode and has no remote endpoint by
construction). Every store lives under the fixture's ``data/tmp`` directory
inside ``D:\\rag-platform``; tests close their clients and the fixture
removes only the test's own directory.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from rag_platform.config import PROJECT_ROOT
from rag_platform.db.sqlite import PathContainmentError
from rag_platform.vectorstore import (
    CollectionError,
    InvalidFilterError,
    InvalidPointError,
    InvalidVectorSizeError,
    PayloadMatch,
    Point,
    QdrantLocalStore,
    VectorStoreError,
)

_V = 4  # explicit test dimension - no embedding model involved (Phase 2 task)

_U1 = "11111111-1111-1111-1111-111111111111"
_U2 = "22222222-2222-2222-2222-222222222222"
_U3 = "33333333-3333-3333-3333-333333333333"
_ABSENT = "99999999-9999-4999-8999-999999999999"  # valid UUID, never inserted


def _vec(x: float, y: float = 0.0) -> list[float]:
    return [x, y, 0.0, 0.0]


@pytest.fixture
def store(storage_tmp: Path) -> QdrantLocalStore:
    client_store = QdrantLocalStore(storage_tmp / "qdrant", vector_size=_V)
    try:
        yield client_store  # type: ignore[misc]
    finally:
        client_store.close()


# --- containment and constructor validation --------------------------------


def test_storage_tmp_is_inside_project_on_d(storage_tmp: Path) -> None:
    assert storage_tmp.drive == PROJECT_ROOT.drive == "D:"
    assert storage_tmp.is_relative_to(PROJECT_ROOT)
    assert storage_tmp.parent == PROJECT_ROOT / "data" / "tmp"


def test_constructor_creates_local_store_under_project(storage_tmp: Path) -> None:
    path = storage_tmp / "qdrant"
    with QdrantLocalStore(path, vector_size=_V) as created:
        assert created.path == path.resolve()
        assert created.path.is_relative_to(PROJECT_ROOT)
        assert created.vector_size == _V
        assert created.distance == "Cosine"
        # Reads are total even before any collection exists:
        assert created.search("never_created", _vec(1.0)) == []
        assert created.count("never_created") == 0


def test_constructor_validates_vector_size_and_distance(storage_tmp: Path) -> None:
    for bad_size in (0, -1, True, "4", 4.0, 4097):
        with pytest.raises(InvalidVectorSizeError):
            QdrantLocalStore(storage_tmp / "never_created", vector_size=bad_size)  # type: ignore[arg-type]
    with pytest.raises(InvalidVectorSizeError, match="distance"):
        QdrantLocalStore(
            storage_tmp / "never_created", vector_size=_V, distance="Manhattan"
        )
    assert not (storage_tmp / "never_created").exists()  # rejected before mkdir


def test_constructor_rejects_paths_outside_project(storage_tmp: Path) -> None:
    strays = [
        Path("D:/") / f"phase2_qd_stray_{uuid4().hex}",
        Path(r"C:\Windows") / f"phase2_qd_stray_{uuid4().hex}",
        PROJECT_ROOT,
    ]
    for stray in strays:
        with pytest.raises(PathContainmentError):
            QdrantLocalStore(stray, vector_size=_V)
    for stray in strays[:2]:
        assert not stray.exists()  # validation precedes any mkdir/client


# --- collection lifecycle ---------------------------------------------------


def test_ensure_collection_idempotent_and_mismatch_detected(storage_tmp: Path) -> None:
    path = storage_tmp / "qdrant"
    first = QdrantLocalStore(path, vector_size=_V)
    try:
        first.ensure_collection("docs")
        first.ensure_collection("docs")  # idempotent on matching config
        assert first.collection_exists("docs")
        with pytest.raises(CollectionError, match="collection name"):
            first.ensure_collection("")
        with pytest.raises(CollectionError, match="collection name"):
            first.ensure_collection("  padded  ")
    finally:
        first.close()
    second = QdrantLocalStore(path, vector_size=8)  # same store dir, other size
    try:
        with pytest.raises(CollectionError, match="vector size"):
            second.ensure_collection("docs")
    finally:
        second.close()


# --- add / search roundtrip -------------------------------------------------


def test_add_search_roundtrip_and_deterministic_order(storage_tmp: Path) -> None:
    with QdrantLocalStore(storage_tmp / "qdrant", vector_size=_V) as local:
        local.ensure_collection("docs")
        local.add(
            "docs",
            [
                Point(id=_U1, vector=_vec(1.0, 0.0), payload={"tenant": "alpha"}),
                Point(id=_U2, vector=_vec(0.0, 1.0), payload={"tenant": "beta"}),
                Point(id=_U3, vector=_vec(0.9, 0.1), payload={"tenant": "alpha"}),
            ],
        )
        assert local.count("docs") == 3
        hits = local.search("docs", _vec(1.0, 0.0), limit=3)
        assert [hit.id for hit in hits] == [_U1, _U3, _U2]  # nearest first
        assert hits[0].payload == {"tenant": "alpha"}
        assert hits[0].score == pytest.approx(1.0)  # cosine: identical direction
        assert hits[0].score >= hits[1].score >= hits[2].score  # ordered scores
        assert local.search("docs", _vec(1.0, 0.0), limit=3) == hits  # deterministic


def test_missing_collection_reads_total_writes_fail_loud(storage_tmp: Path) -> None:
    with QdrantLocalStore(storage_tmp / "qdrant", vector_size=_V) as local:
        point = Point(id=_U1, vector=_vec(1.0))
        assert local.search("ghost", _vec(1.0)) == []  # read: empty, not error
        assert local.count("ghost") == 0
        with pytest.raises(CollectionError, match="does not exist"):
            local.add("ghost", [point])
        with pytest.raises(CollectionError, match="does not exist"):
            local.remove("ghost", [_U1])
        # Input validation precedes state checks: bad limit on a missing
        # collection still fails as an input error, not a silent empty read.
        with pytest.raises(VectorStoreError, match="limit"):
            local.search("ghost", _vec(1.0), limit=0)


def test_persistence_survives_close_and_reopen(storage_tmp: Path) -> None:
    path = storage_tmp / "qdrant"
    with QdrantLocalStore(path, vector_size=_V) as writer:
        writer.ensure_collection("docs")
        writer.add(
            "docs",
            [Point(id=_U1, vector=_vec(1.0), payload={"tenant": "alpha", "n": 7})],
        )
    with QdrantLocalStore(path, vector_size=_V) as reader:  # fresh client, same dir
        hits = reader.search("docs", _vec(1.0), limit=5)
        assert [hit.id for hit in hits] == [_U1]
        assert hits[0].payload == {"tenant": "alpha", "n": 7}
        assert reader.count("docs") == 1


def test_remove_reflected_in_search_and_unknown_id_noop(storage_tmp: Path) -> None:
    with QdrantLocalStore(storage_tmp / "qdrant", vector_size=_V) as local:
        local.ensure_collection("docs")
        local.add(
            "docs",
            [
                Point(id=_U1, vector=_vec(1.0)),
                Point(id=_U2, vector=_vec(0.0, 1.0)),
            ],
        )
        local.remove("docs", [_U1])
        assert [hit.id for hit in local.search("docs", _vec(1.0), limit=5)] == [_U2]
        assert local.count("docs") == 1
        local.remove("docs", [_ABSENT])  # valid-but-absent id: documented no-op
        assert local.count("docs") == 1
        with pytest.raises(InvalidPointError, match="non-empty"):
            local.remove("docs", [])
        with pytest.raises(InvalidPointError, match="int id must be >= 0"):
            local.remove("docs", [-1])


# --- payload filtering ------------------------------------------------------


def test_payload_filter_returns_exact_expected_ids(storage_tmp: Path) -> None:
    with QdrantLocalStore(storage_tmp / "qdrant", vector_size=_V) as local:
        local.ensure_collection("docs")
        local.add(
            "docs",
            [
                Point(
                    id=_U1, vector=_vec(1.0), payload={"tenant": "alpha", "kind": "doc"}
                ),
                Point(
                    id=_U2,
                    vector=_vec(0.5, 0.5),
                    payload={"tenant": "beta", "kind": "doc"},
                ),
                Point(
                    id=_U3,
                    vector=_vec(0.0, 1.0),
                    payload={"tenant": "beta", "kind": "note"},
                ),
            ],
        )

        def ids(filter_: list[PayloadMatch]) -> list[str]:
            return [
                str(hit.id)
                for hit in local.search(
                    "docs", _vec(1.0), limit=10, payload_filter=filter_
                )
            ]

        assert sorted(ids([PayloadMatch("tenant", "beta")])) == sorted([_U2, _U3])
        assert ids([PayloadMatch("tenant", "beta"), PayloadMatch("kind", "note")]) == [
            _U3
        ]  # AND-combined
        assert ids([PayloadMatch("tenant", "alpha")]) == [_U1]
        assert ids([PayloadMatch("tenant", "gamma")]) == []  # deterministic empty
        assert ids([PayloadMatch("tenant", "gamma")]) == []  # stable across runs
        assert local.search("docs", _vec(1.0), limit=10) == [
            local.search("docs", _vec(1.0), limit=10)[i] for i in range(3)
        ]  # unfiltered search returns all three, same order every time


def test_invalid_filters_rejected_before_query(storage_tmp: Path) -> None:
    with QdrantLocalStore(storage_tmp / "qdrant", vector_size=_V) as local:
        local.ensure_collection("docs")
        local.add(
            "docs", [Point(id=_U1, vector=_vec(1.0), payload={"tenant": "alpha"})]
        )
        bad_filters: list[object] = [
            [],  # empty sequence is not "no filter"
            "tenant=alpha",  # raw string
            ["tenant=alpha"],  # not PayloadMatch entries
            PayloadMatch("tenant", "alpha"),  # not a sequence of matches
            [PayloadMatch("", "x")],  # blank key
            [PayloadMatch(" tenant", "x")],  # whitespace-sensitive key
            [PayloadMatch("tenant ", "x")],
            [PayloadMatch("tenant", 1.5)],  # MatchValue has no float support
            [PayloadMatch("tenant", None)],
            [PayloadMatch("tenant", ["alpha"])],
        ]
        for bad in bad_filters:
            with pytest.raises(InvalidFilterError):
                local.search("docs", _vec(1.0), payload_filter=bad)  # type: ignore[arg-type]
        # Valid-but-unmatched filter still returns the deterministic empty set:
        assert (
            local.search(
                "docs", _vec(1.0), payload_filter=[PayloadMatch("kind", "doc")]
            )
            == []
        )


# --- invalid input rejection (nothing is ever written) ----------------------


def test_invalid_vectors_rejected_and_nothing_written(storage_tmp: Path) -> None:
    with QdrantLocalStore(storage_tmp / "qdrant", vector_size=_V) as local:
        local.ensure_collection("docs")
        bad_vectors: list[object] = [
            [1.0, 0.0, 0.0],  # wrong length
            [],  # empty
            [1.0, float("nan"), 0.0, 0.0],  # non-finite
            [1.0, float("inf"), 0.0, 0.0],
            ["a", 0.0, 0.0, 0.0],  # non-numeric
            [True, 0.0, 0.0, 0.0],  # bool is not a vector number
            "abcd",  # a str is a Sequence - must still fail as non-numeric
        ]
        for bad in bad_vectors:
            with pytest.raises(InvalidPointError):
                local.add("docs", [Point(id=_U1, vector=bad)])  # type: ignore[arg-type]
        with pytest.raises(InvalidPointError, match="expected 4 values"):
            local.search("docs", _vec(1.0, 0.0)[:3])
        with pytest.raises(InvalidPointError, match="finite"):
            local.search("docs", [1.0, float("nan"), 0.0, 0.0])
        assert local.count("docs") == 0  # batch validation precedes every write


def test_invalid_ids_rejected_and_batch_stays_atomic(storage_tmp: Path) -> None:
    with QdrantLocalStore(storage_tmp / "qdrant", vector_size=_V) as local:
        local.ensure_collection("docs")
        local.add(  # valid id forms
            "docs",
            [Point(id=0, vector=_vec(1.0)), Point(id=_U1, vector=_vec(0.0, 1.0))],
        )
        assert local.count("docs") == 2
        for bad_id in (-1, True, "not-a-uuid", 1.5, {"a": 1}):
            with pytest.raises(InvalidPointError):
                local.add("docs", [Point(id=bad_id, vector=_vec(1.0))])  # type: ignore[arg-type]
        # One invalid point invalidates the whole batch: the valid point
        # ahead of it never reaches the store.
        with pytest.raises(InvalidPointError):
            local.add(
                "docs",
                [Point(id=_U2, vector=_vec(1.0)), Point(id=-7, vector=_vec(1.0))],
            )
        assert local.count("docs") == 2  # unchanged - no partial write


def test_invalid_payloads_rejected(storage_tmp: Path) -> None:
    with QdrantLocalStore(storage_tmp / "qdrant", vector_size=_V) as local:
        local.ensure_collection("docs")
        bad_payloads: list[object] = [
            {"tenant": None},
            {"tenant": ["alpha"]},
            {"tenant": {"nested": 1}},
            {123: "x"},  # non-str key
            {"": "x"},  # blank key
            {" tenant": "x"},  # whitespace-sensitive keys
            {"tenant": float("nan")},  # non-finite float
            ["not-a-mapping"],  # payload not a mapping at all
        ]
        for bad in bad_payloads:
            with pytest.raises(InvalidPointError):
                local.add(
                    "docs",
                    [Point(id=_U1, vector=_vec(1.0), payload=bad)],  # type: ignore[arg-type]
                )
        assert local.count("docs") == 0


def test_empty_batches_and_limit_bounds_rejected(storage_tmp: Path) -> None:
    with QdrantLocalStore(storage_tmp / "qdrant", vector_size=_V) as local:
        local.ensure_collection("docs")
        for bad_points in ([], "nope", {"a": 1}):
            with pytest.raises(InvalidPointError, match="non-empty"):
                local.add("docs", bad_points)  # type: ignore[arg-type]
        for bad_limit in (0, -1, 101, True, 2.5):
            with pytest.raises(VectorStoreError, match="limit"):
                local.search("docs", _vec(1.0), limit=bad_limit)  # type: ignore[arg-type]


# --- lifecycle --------------------------------------------------------------


def test_close_is_idempotent_and_operations_fail_loud_after_close(
    storage_tmp: Path,
) -> None:
    path = storage_tmp / "qdrant"
    local = QdrantLocalStore(path, vector_size=_V)
    local.ensure_collection("docs")
    local.close()
    local.close()  # idempotent
    for operation in (
        lambda: local.search("docs", _vec(1.0)),
        lambda: local.add("docs", [Point(id=_U1, vector=_vec(1.0))]),
        lambda: local.remove("docs", [_U1]),
        lambda: local.count("docs"),
        lambda: local.collection_exists("docs"),
        lambda: local.ensure_collection("docs"),
    ):
        with pytest.raises(VectorStoreError, match="closed"):
            operation()
    with QdrantLocalStore(path, vector_size=_V) as reopened:  # context manager
        reopened.ensure_collection("docs")
    with pytest.raises(VectorStoreError, match="closed"):
        reopened.search("docs", _vec(1.0))  # closed by __exit__
