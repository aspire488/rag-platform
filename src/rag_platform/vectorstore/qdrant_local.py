"""Qdrant local-mode vector store adapter (plan v2.4 Phase 2; section 6
dense store, section 11 ``vectorstore\\``, section 12 persistence/filter row).

Scope and guarantees (Phase 2):

- **Local mode only.** The adapter takes a filesystem path and constructs
  ``QdrantClient(path=...)``. No URL, API key, or remote-endpoint parameter
  exists anywhere in this interface, so a remote/cloud connection is
  structurally impossible - never a silent fallback (Z4/I3; the plan's dense
  store is "embedded, path on D:").
- **Path containment (Z9).** The store path is resolved against
  PROJECT_ROOT and must stay strictly inside ``D:\\rag-platform``; the plan's
  default runtime location is ``data\\qdrant\\`` (section 11). Rejected paths
  create nothing.
- **Explicit collection configuration.** Vector size and distance metric are
  validated at construction; an existing collection with a different size or
  named-vector config is refused (:class:`CollectionError`) instead of being
  silently reused. Default distance is Cosine (Qdrant's default; the plan
  does not specify a metric - recorded here as an implementation decision,
  revisitable when Phase 3/5 embeddings exist).
- **Dimension is an explicit constructor parameter** (Phase 2 task section
  4): the plan fixes no vector dimension before Phase 3, so none is invented
  here and no embedding model is ever imported or loaded by this module.
- **Deterministic edge-behaviour contract:**
    - ``search`` / ``count`` on a missing collection -> empty list / 0
      (reads are total: a collection that was never created has no vectors)
    - ``add`` / ``remove`` on a missing collection -> :class:`CollectionError`
      (writes fail loud; no implicit creation that could mask corruption)
    - ``remove`` of valid ids that do not exist -> documented no-op
    - invalid vector / id / payload / filter / limit -> typed error raised
      **before** any write reaches the store (all-or-nothing batch validation)
    - invalid inputs are rejected before state checks: input errors surface
      even when the collection is missing
- **Lifecycle.** ``close()`` (idempotent) or the context-manager protocol;
  any operation after close raises :class:`VectorStoreError` - use-after-close
  is never a silent no-op.

Deliberately out of scope (later phases): ingestion, embeddings, hybrid
retrieval/RRF, tenant/ACL payload schemas, generation, provider routing,
and any network path. The store interface stays narrow: collection
init, upsert, removal, count, and filtered search.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models

from rag_platform.db.sqlite import resolve_under_project

#: Valid distance metrics (Qdrant names). The plan does not choose one;
#: Cosine is Qdrant's default and the adapter default.
_DISTANCE_MAP: dict[str, models.Distance] = {
    "Cosine": models.Distance.COSINE,
    "Euclid": models.Distance.EUCLID,
    "Dot": models.Distance.DOT,
}

#: I7 (bounded everything) sanity cap on vector dimension. MiniLM is 384
#: dims; anything above this is a configuration bug, not a real local model.
_MAX_VECTOR_SIZE = 4096

#: I7 cap for a single adapter search call (plan section 10.1: top-k max 20
#: for retrieval; the adapter allows headroom but refuses unbounded limits).
_MAX_SEARCH_LIMIT = 100


class VectorStoreError(Exception):
    """Base error for Qdrant local-store operations."""


class InvalidVectorSizeError(VectorStoreError):
    """Constructor vector size (or distance metric) failed validation."""


class InvalidPointError(VectorStoreError):
    """A point id, vector, or payload failed validation before any write."""


class InvalidFilterError(VectorStoreError):
    """A payload filter failed validation before any query was issued."""


class CollectionError(VectorStoreError):
    """Collection missing for a write, misconfigured, or name invalid."""


@dataclass(frozen=True)
class PayloadMatch:
    """One exact-match payload condition (AND-combined when several given).

    Values are ``str``/``int``/``bool`` only - Qdrant ``MatchValue``
    semantics (no float matching); floats may be *stored* in payloads but
    cannot be filtered on. Keys are exact and whitespace-sensitive.
    """

    key: str
    value: str | int | bool


@dataclass(frozen=True)
class Point:
    """One point to upsert: id, dense vector, optional flat payload.

    Validation happens at the storage boundary (:meth:`QdrantLocalStore.add`)
    where the store's dimension is known.
    """

    id: int | str
    vector: Sequence[float]
    payload: Mapping[str, str | int | float | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    """One search result: point id, similarity score, payload."""

    id: int | str
    score: float
    payload: dict[str, Any]


def _validate_collection_name(name: object) -> str:
    if not isinstance(name, str) or name != name.strip() or not name:
        raise CollectionError(
            f"collection name must be a non-blank string without surrounding "
            f"whitespace; got {name!r}"
        )
    return name


def _validate_point_id(value: object, *, field_name: str) -> int | str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise InvalidPointError(
            f"{field_name}: id must be a non-negative int or a UUID string; got {value!r}"
        )
    if isinstance(value, int):
        if value < 0:
            raise InvalidPointError(f"{field_name}: int id must be >= 0; got {value}")
        return value
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise InvalidPointError(
            f"{field_name}: string id must be a UUID; got {value!r}"
        ) from exc
    return value


def _validate_vector(
    vector: object, *, expected_size: int, field_name: str
) -> list[float]:
    if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence):
        raise InvalidPointError(f"{field_name}: vector must be a sequence of numbers")
    if len(vector) != expected_size:
        raise InvalidPointError(
            f"{field_name}: expected {expected_size} values, got {len(vector)}"
        )
    values: list[float] = []
    for item in vector:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise InvalidPointError(
                f"{field_name}: vector values must be numeric; got {item!r}"
            )
        number = float(item)
        if not math.isfinite(number):
            raise InvalidPointError(
                f"{field_name}: vector values must be finite; got {item!r}"
            )
        values.append(number)
    return values


def _validate_payload(payload: object, *, field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise InvalidPointError(
            f"{field_name}: payload must be a mapping; got {payload!r}"
        )
    validated: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key or key != key.strip():
            raise InvalidPointError(
                f"{field_name}: payload keys must be non-blank exact strings; got {key!r}"
            )
        if isinstance(value, bool) or isinstance(value, str):
            validated[key] = value
        elif isinstance(value, int):
            validated[key] = value
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise InvalidPointError(
                    f"{field_name}: payload float values must be finite; got {value!r}"
                )
            validated[key] = value
        else:
            raise InvalidPointError(
                f"{field_name}: payload values must be str/int/float/bool; "
                f"got {key}={value!r}"
            )
    return validated


def _validate_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise VectorStoreError(f"limit must be an int; got {limit!r}")
    if not 1 <= limit <= _MAX_SEARCH_LIMIT:
        raise VectorStoreError(
            f"limit must be within 1..{_MAX_SEARCH_LIMIT}; got {limit}"
        )
    return limit


def _build_filter(matches: object) -> models.Filter:
    if isinstance(matches, (str, bytes)) or not isinstance(matches, Sequence):
        raise InvalidFilterError(
            "payload_filter must be a sequence of PayloadMatch "
            "(pass None for no filter)"
        )
    if len(matches) == 0:
        raise InvalidFilterError(
            "payload_filter must not be empty; pass None for no filter"
        )
    conditions: list[models.FieldCondition] = []
    for match in matches:
        if not isinstance(match, PayloadMatch):
            raise InvalidFilterError(f"expected PayloadMatch entries; got {match!r}")
        if (
            not isinstance(match.key, str)
            or not match.key
            or match.key != match.key.strip()
        ):
            raise InvalidFilterError(
                f"filter key must be a non-blank exact string; got {match.key!r}"
            )
        if (
            isinstance(match.value, bool)
            or isinstance(match.value, int)
            or isinstance(match.value, str)
        ):
            pass  # Qdrant MatchValue accepts keyword/integer/boolean (no float)
        else:
            raise InvalidFilterError(
                f"filter value must be str/int/bool; got {match.value!r}"
            )
        conditions.append(
            models.FieldCondition(
                key=match.key, match=models.MatchValue(value=match.value)
            )
        )
    return models.Filter(must=conditions)


class QdrantLocalStore:
    """Narrow, typed adapter over a Qdrant client in embedded local mode."""

    def __init__(
        self,
        path: str | Path,
        *,
        vector_size: int,
        distance: str = "Cosine",
    ) -> None:
        # Input validation strictly before any filesystem side effect.
        if isinstance(vector_size, bool) or not isinstance(vector_size, int):
            raise InvalidVectorSizeError(
                f"vector_size must be an int; got {vector_size!r}"
            )
        if not 1 <= vector_size <= _MAX_VECTOR_SIZE:
            raise InvalidVectorSizeError(
                f"vector_size must be within 1..{_MAX_VECTOR_SIZE}; got {vector_size}"
            )
        if distance not in _DISTANCE_MAP:
            raise InvalidVectorSizeError(
                f"distance must be one of {sorted(_DISTANCE_MAP)}; got {distance!r}"
            )
        self._path = resolve_under_project(path)  # Z9: raises, creates nothing
        if self._path.is_file():
            raise VectorStoreError(f"store path is an existing file: {self._path}")
        self._path.mkdir(parents=True, exist_ok=True)
        self._vector_size = vector_size
        self._distance = distance
        self._closed = False
        # Local mode only: path-based client. There is deliberately no
        # url/api_key constructor path - remote endpoints are unreachable
        # through this interface by construction.
        self._client = QdrantClient(path=str(self._path))

    # --- lifecycle -----------------------------------------------------

    @property
    def path(self) -> Path:
        """Resolved local storage directory (inside the project root)."""
        return self._path

    @property
    def vector_size(self) -> int:
        return self._vector_size

    @property
    def distance(self) -> str:
        return self._distance

    def _require_open(self) -> None:
        if self._closed:
            raise VectorStoreError(f"store at {self._path} is closed")

    def close(self) -> None:
        """Release the local store (idempotent; safe to call twice)."""
        if not self._closed:
            self._client.close()
            self._closed = True

    def __enter__(self) -> QdrantLocalStore:
        self._require_open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- collections ---------------------------------------------------

    def collection_exists(self, name: str) -> bool:
        """Whether *name* exists (validated; raises after close)."""
        collection = _validate_collection_name(name)
        self._require_open()
        return bool(self._client.collection_exists(collection))

    def ensure_collection(self, name: str) -> None:
        """Create *name* with this store's explicit config, or validate it.

        An existing collection must match this store's vector size exactly;
        a mismatch (or a named-vector collection this adapter does not manage)
        raises :class:`CollectionError` rather than proceeding against
        incompatible configuration.
        """
        collection = _validate_collection_name(name)
        self._require_open()
        if self._client.collection_exists(collection):
            info = self._client.get_collection(collection)
            vectors = info.config.params.vectors
            if isinstance(vectors, dict):
                raise CollectionError(
                    f"collection {collection!r} uses named vectors, which this "
                    "adapter does not manage"
                )
            existing = int(vectors.size)
            if existing != self._vector_size:
                raise CollectionError(
                    f"collection {collection!r} has vector size {existing}, but this "
                    f"store is configured with {self._vector_size}"
                )
            return
        self._client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(
                size=self._vector_size,
                distance=_DISTANCE_MAP[self._distance],
            ),
        )

    # --- writes ----------------------------------------------------------

    def add(self, collection: str, points: Sequence[Point]) -> None:
        """Upsert *points* into *collection* (all validated before one call).

        The batch is validated in full first: a single invalid point means
        nothing is written. The collection must already exist - writes never
        implicitly create state.
        """
        name = _validate_collection_name(collection)
        self._require_open()
        if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
            raise InvalidPointError("points must be a non-empty sequence of Point")
        if len(points) == 0:
            raise InvalidPointError("points must be a non-empty sequence of Point")
        structs: list[models.PointStruct] = []
        for index, point in enumerate(points):
            where = f"points[{index}]"
            if not isinstance(point, Point):
                raise InvalidPointError(f"{where}: expected Point; got {point!r}")
            point_id = _validate_point_id(point.id, field_name=where)
            vector = _validate_vector(
                point.vector, expected_size=self._vector_size, field_name=where
            )
            payload = _validate_payload(point.payload, field_name=where)
            structs.append(
                models.PointStruct(id=point_id, vector=vector, payload=payload)
            )
        if not self._client.collection_exists(name):
            raise CollectionError(
                f"collection {name!r} does not exist; call ensure_collection first"
            )
        self._client.upsert(collection_name=name, points=structs, wait=True)

    def remove(self, collection: str, ids: Sequence[int | str]) -> None:
        """Delete *ids* from *collection*.

        Ids that are valid but absent are a documented no-op (Qdrant
        semantics). The collection must exist; ids are validated first.
        """
        name = _validate_collection_name(collection)
        self._require_open()
        if isinstance(ids, (str, bytes)) or not isinstance(ids, Sequence):
            raise InvalidPointError("ids must be a non-empty sequence of point ids")
        if len(ids) == 0:
            raise InvalidPointError("ids must be a non-empty sequence of point ids")
        validated = [
            _validate_point_id(value, field_name=f"ids[{index}]")
            for index, value in enumerate(ids)
        ]
        if not self._client.collection_exists(name):
            raise CollectionError(
                f"collection {name!r} does not exist; call ensure_collection first"
            )
        self._client.delete(
            collection_name=name,
            points_selector=models.PointIdsList(points=validated),
            wait=True,
        )

    # --- reads -----------------------------------------------------------

    def search(
        self,
        collection: str,
        vector: Sequence[float],
        *,
        limit: int = 8,
        payload_filter: Sequence[PayloadMatch] | None = None,
    ) -> list[SearchHit]:
        """Search *collection* for the nearest neighbours of *vector*.

        Input validation (limit, query vector, filter) happens first - even
        against a missing collection. A missing collection then yields ``[]``
        (reads are total); an empty filter match yields ``[]`` deterministically.
        Results are ordered by descending similarity score.
        """
        name = _validate_collection_name(collection)
        self._require_open()
        checked_limit = _validate_limit(limit)
        query = _validate_vector(
            vector, expected_size=self._vector_size, field_name="query vector"
        )
        query_filter = (
            _build_filter(payload_filter) if payload_filter is not None else None
        )
        if not self._client.collection_exists(name):
            return []
        result = self._client.query_points(
            collection_name=name,
            query=query,
            limit=checked_limit,
            query_filter=query_filter,
            with_payload=True,
        )
        return [
            SearchHit(
                id=hit.id, score=float(hit.score), payload=dict(hit.payload or {})
            )
            for hit in result.points
        ]

    def count(self, collection: str) -> int:
        """Exact point count; a missing collection counts as 0 (reads total)."""
        name = _validate_collection_name(collection)
        self._require_open()
        if not self._client.collection_exists(name):
            return 0
        return int(self._client.count(collection_name=name, exact=True).count)
