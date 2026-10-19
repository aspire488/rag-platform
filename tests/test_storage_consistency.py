"""Phase 2 cross-store consistency boundary test (plan v2.4; I9).

The canonical plan defines **no atomic cross-store operation** at Phase 2:
SQLite (WAL) and the Qdrant directory are two independent local sources of
truth (I9), and the document-deletion contract that spans both is only
specified when ingestion lands (Phase 4) together with M2 access control.
This test therefore documents the boundary honestly instead of inventing
transactional atomicity: removing a vector touches Qdrant only, and SQLite
rows are unaffected - each store's deletion semantics stay independent and
observable until the plan defines a combined contract.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from rag_platform.db import initialize_database
from rag_platform.vectorstore import Point, QdrantLocalStore

_DOC_ID = "d1"
_POINT_ID = "55555555-5555-5555-5555-555555555555"


def test_vector_removal_does_not_touch_sqlite_rows(storage_tmp: Path) -> None:
    with closing(initialize_database(storage_tmp / "cross.db")) as conn:
        conn.execute(
            "INSERT INTO chunk_fts (chunk_id, doc_id, content) VALUES (?, ?, ?)",
            ("c1", _DOC_ID, "the walrus was here"),
        )
        with QdrantLocalStore(storage_tmp / "qdrant", vector_size=4) as store:
            store.ensure_collection("chunks")
            store.add(
                "chunks",
                [
                    Point(
                        id=_POINT_ID,
                        vector=[1.0, 0.0, 0.0, 0.0],
                        payload={"doc_id": _DOC_ID},
                    )
                ],
            )
            assert store.count("chunks") == 1
            store.remove("chunks", [_POINT_ID])
            # Qdrant side reflects the removal:
            assert store.count("chunks") == 0
            assert store.search("chunks", [1.0, 0.0, 0.0, 0.0]) == []
            # SQLite side is untouched: the two stores are independent;
            # no cross-store atomicity is claimed or implied here (I9).
            fts_rows = conn.execute(
                "SELECT chunk_id FROM chunk_fts WHERE doc_id = ?", (_DOC_ID,)
            ).fetchall()
            assert [row["chunk_id"] for row in fts_rows] == ["c1"]
