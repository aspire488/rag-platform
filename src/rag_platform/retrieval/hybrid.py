"""Hybrid retrieval orchestration (plan v2.4 Phase 5: dense + FTS5 + RRF,
post-verify stub, dense score floor, top-k hard max 20).

Stage order per query (the plan section 5 retrieval stage, decomposed and
testable): validate bounds -> sanitize -> lexical leg -> dense leg (when
operational) -> RRF fusion -> dense score floor -> hydrate provenance
from SQLite -> top-k truncation -> post-verification hook -> report.

Operational honesty (Z8):

- Dense is operational iff an embedder AND a vector store are injected
  AND the owner-approved score floor is set AND the dense collection is
  verified to use the Cosine metric (remediation F1: a floor validated
  as cosine similarity must never be compared against scores from a
  Dot/Euclid collection; such a store degrades to lexical-only with the
  reason ``non_cosine_collection:<Metric>``). Otherwise the query runs
  lexical-only and the report names the first blocker
  (``no_embedder`` / ``no_vector_store`` / ``score_floor_unset`` /
  ``non_cosine_collection:<Metric>``); an embedder raising the Phase 3
  typed errors degrades THAT query to lexical-only
  (``embedder_error:<Class>``) without hiding the failure.
- The floor applies ONLY to candidates carrying a dense cosine
  similarity; lexical-only candidates are never judged by it and are
  never claimed to have passed it. All dense-evidenced candidates
  excluded -> ``insufficient_evidence``; nothing to rank ->
  ``no_candidates``.
- Post-verification output is REVALIDATED (remediation F4): a
  non-sequence return, a non-``RetrievedChunk`` item, or a structurally
  invalid one raises the typed :class:`PostVerifyError` (the retriever
  never serves unvalidated verifier output); output beyond ``top_k`` is
  truncated so the bound is structural; ``post_verify_dropped`` is
  clamped at zero so counts are never negative. Legitimate filtering
  (dropping results) is preserved.
- Injection metadata is advisory (I11): carried into results, never
  gating or reordering them. Source content stays untrusted.
- A tokenless/empty query is a deterministic empty outcome from BOTH
  legs (a dense embedding of punctuation-only text carries no evidence).

Retrieval has no generation dependency (retrieval-only is first-class:
plan section 5.4 / Z11) and opens no sockets: SQLite reads, the local
in-process vector store, and an injected embedder only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from rag_platform.config import Settings
from rag_platform.embeddings.minilm import EmbeddingError
from rag_platform.ingestion.store import CorpusStore
from rag_platform.retrieval.dense import Embedder, dense_search
from rag_platform.retrieval.errors import PostVerifyError, QueryValidationError
from rag_platform.retrieval.fusion import reciprocal_rank_fusion
from rag_platform.retrieval.lexical import build_match_query, lexical_search
from rag_platform.retrieval.post_verify import PostVerifier, pass_through_post_verify
from rag_platform.retrieval.types import (
    DenseHit,
    FusedChunk,
    RetrievedChunk,
    RetrievalReport,
)
from rag_platform.vectorstore.qdrant_local import QdrantLocalStore

#: Default dense collection (shared with the indexer by name).
DEFAULT_COLLECTION = "chunks"

#: Hard runtime cap for a per-request top_k argument (plan section 10.1:
#: top-k default 8, max 20 - the validated Settings default is the source
#: of truth; this guard makes direct library calls equally bounded).
_MAX_TOP_K = 20


def _finding_count(raw: object) -> int:
    """Persisted scan JSON -> finding count (0 for NULL/unparseable).

    Phase 4 stores ``{"findings": [...], "truncated": bool}`` or NULL.
    """
    if not isinstance(raw, str) or not raw:
        return 0
    try:
        parsed = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(parsed, dict):
        return 0
    findings = parsed.get("findings")
    return len(findings) if isinstance(findings, list) else 0


def _validate_retrieved_chunk(item: RetrievedChunk) -> None:
    """Structural (not semantic) contract for one verifier output item.

    Remediation F4: catches fabricated or garbled ``RetrievedChunk``
    objects at the retriever boundary. Field-level semantic validation
    (e.g. whether a span really exists in the source document) remains
    the future M2 verifier's responsibility, not this stage's.
    """
    for field_name in ("ordinal", "char_start", "char_end", "rank"):
        value = getattr(item, field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PostVerifyError(
                f"post_verify item field {field_name!r} must be a "
                f"non-negative int; got {type(value).__name__}"
            )
    if item.char_end <= item.char_start:
        raise PostVerifyError("post_verify item has an empty or inverted char range")
    if not isinstance(item.content, str) or not isinstance(item.source_path, str):
        raise PostVerifyError("post_verify item content/source_path must be strings")


class HybridRetriever:
    """Deterministic hybrid retriever over one corpus DB (+ optional store).

    ``store=None`` makes the dense leg structurally unavailable
    (lexical-only by construction). ``post_verify`` runs exactly once per
    query, after truncation; the default is the honest pass-through (no
    authorization guarantee - see ``post_verify``).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        settings: Settings,
        embedder: Embedder | None = None,
        store: QdrantLocalStore | None = None,
        collection: str = DEFAULT_COLLECTION,
        post_verify: PostVerifier = pass_through_post_verify,
    ) -> None:
        if not callable(post_verify):
            raise TypeError("post_verify must be callable")
        self._conn = conn
        self._settings = settings
        self._embedder = embedder
        self._store = store
        self._collection = collection
        self._post_verify = post_verify

    def retrieve(self, query: str, *, top_k: int | None = None) -> RetrievalReport:
        """Hybrid retrieval for *query*; empty evidence is an outcome, not an error.

        Raises :class:`QueryValidationError` for invalid runtime bounds
        (non-string query; query over the configured character cap;
        top_k outside 1..20). Vector-store misconfiguration propagates
        the store's typed error (never silently degraded); embedder
        failures degrade to lexical-only with the reason reported.
        """
        settings = self._settings
        k = settings.retrieval_top_k if top_k is None else top_k
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= _MAX_TOP_K:
            raise QueryValidationError(
                f"top_k must be an int in 1..{_MAX_TOP_K}; got {k!r}"
            )
        # Validates the character cap (raises) and detects a tokenless query.
        match_expr = build_match_query(
            query, max_query_chars=settings.retrieval_query_max_chars
        )

        # Dense gating: the first blocker is the reported reason.
        dense_operational = True
        dense_reason: str | None = None
        if self._embedder is None:
            dense_operational, dense_reason = False, "no_embedder"
        elif self._store is None:
            dense_operational, dense_reason = False, "no_vector_store"
        elif settings.retrieval_score_floor is None:
            dense_operational, dense_reason = False, "score_floor_unset"
        else:
            # Remediation F1: the floor is validated as cosine similarity,
            # so it may only be compared against scores from a verified
            # Cosine collection. A non-Cosine metric is a safe
            # non-operational outcome (lexical-only, reason reported) -
            # never a silent assumption. A missing collection stays legal:
            # dense reads are total and simply return no candidates. Metric
            # inspection failures for unmanaged collections (named vectors)
            # propagate as the store's typed CollectionError.
            distance = self._store.collection_distance(self._collection)
            if distance is not None and distance != "Cosine":
                dense_operational, dense_reason = (
                    False,
                    f"non_cosine_collection:{distance}",
                )

        lexical_hits = lexical_search(
            self._conn,
            query,
            limit=settings.retrieval_lexical_candidates,
            max_query_chars=settings.retrieval_query_max_chars,
        )
        dense_hits: list[DenseHit] = []
        if not match_expr:
            # Tokenless query: both legs are deterministic no-candidates.
            return self._report(
                mode="hybrid" if dense_operational else "lexical_only",
                lexical_only_reason=dense_reason,
                outcome="no_candidates",
                top_k=k,
                lexical_hits=0,
                dense_hits=0,
                fused_candidates=0,
                below_floor_excluded=0,
                unhydrated_skipped=0,
                post_verify_dropped=0,
                score_floor=None,
                results=[],
            )
        if dense_operational:
            try:
                dense_hits = dense_search(
                    self._store,
                    self._collection,
                    self._embedder,
                    query,
                    limit=settings.retrieval_dense_candidates,
                )
            except EmbeddingError as exc:
                dense_operational = False
                dense_reason = f"embedder_error:{exc.__class__.__name__}"

        fused = reciprocal_rank_fusion(lexical_hits, dense_hits)
        floor = settings.retrieval_score_floor
        excluded = 0
        if floor is not None and dense_hits:
            kept = [
                candidate
                for candidate in fused
                if candidate.dense_cosine_similarity is None
                or candidate.dense_cosine_similarity >= floor
            ]
            excluded = len(fused) - len(kept)
        else:
            kept = fused
        if not fused:
            outcome = "no_candidates"
        elif not kept:
            outcome = "insufficient_evidence"
        else:
            outcome = "ok"

        results, unhydrated = self._hydrate(kept[:k])
        # Remediation F4: verifier output is revalidated - never served raw.
        verified = self._validate_post_verify(self._post_verify(results), top_k=k)
        return self._report(
            mode="hybrid" if dense_operational else "lexical_only",
            lexical_only_reason=dense_reason,
            outcome=outcome,
            top_k=k,
            lexical_hits=len(lexical_hits),
            dense_hits=len(dense_hits),
            fused_candidates=len(fused),
            below_floor_excluded=excluded,
            unhydrated_skipped=unhydrated,
            post_verify_dropped=max(0, len(results) - len(verified)),
            score_floor=floor if (floor is not None and dense_hits) else None,
            results=verified,
        )

    def _hydrate(
        self, candidates: list[FusedChunk]
    ) -> tuple[list[RetrievedChunk], int]:
        """Attach citation-grade provenance from SQLite (DB is the truth).

        Candidates whose persisted row no longer exists (a stale vector
        point) are skipped and counted - never fabricated.
        """
        reader = CorpusStore(self._conn)
        chunks_by_doc: dict[str, dict[str, sqlite3.Row]] = {}
        documents: dict[str, sqlite3.Row | None] = {}
        results: list[RetrievedChunk] = []
        unhydrated = 0
        for position, candidate in enumerate(candidates, start=1):
            rows = chunks_by_doc.get(candidate.doc_id)
            if rows is None:
                rows = {
                    row["chunk_id"]: row for row in reader.get_chunks(candidate.doc_id)
                }
                chunks_by_doc[candidate.doc_id] = rows
                documents[candidate.doc_id] = reader.get_document(candidate.doc_id)
            row = rows.get(candidate.chunk_id)
            document = documents[candidate.doc_id]
            if row is None or document is None:
                unhydrated += 1
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=candidate.chunk_id,
                    doc_id=candidate.doc_id,
                    ordinal=int(row["ordinal"]),
                    source_path=str(document["source_path"]),
                    content=str(row["content"]),
                    char_start=int(row["char_start"]),
                    char_end=int(row["char_end"]),
                    rank=position,
                    fused_score=candidate.fused_score,
                    dense_cosine_similarity=candidate.dense_cosine_similarity,
                    lexical_bm25=candidate.lexical_bm25,
                    injection_quarantined=bool(row["injection_quarantined"]),
                    injection_finding_count=_finding_count(row["injection_findings"]),
                )
            )
        return results, unhydrated

    @staticmethod
    def _validate_post_verify(raw: object, *, top_k: int) -> list[RetrievedChunk]:
        """Revalidate post-verification output (remediation F4).

        Contract: a sequence of structurally valid ``RetrievedChunk``
        objects. Output beyond *top_k* is truncated to the first ``top_k``
        items so the result bound is structural, not conventional;
        legitimate filtering (returning fewer items) is preserved and
        counted by the caller. A non-sequence return, a non-``RetrievedChunk``
        item, or a structurally invalid one raises the typed
        :class:`PostVerifyError` - deterministic, with type labels only in
        the message (no content, paths, or values echoed).
        """
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise PostVerifyError(
                f"post_verify must return a sequence; got {type(raw).__name__}"
            )
        verified: list[RetrievedChunk] = []
        for item in raw:
            if len(verified) >= top_k:
                break  # bound enforced structurally; extras never served
            if not isinstance(item, RetrievedChunk):
                raise PostVerifyError(
                    "post_verify returned a non-RetrievedChunk item: "
                    f"{type(item).__name__}"
                )
            _validate_retrieved_chunk(item)
            verified.append(item)
        return verified

    @staticmethod
    def _report(
        *,
        mode: str,
        lexical_only_reason: str | None,
        outcome: str,
        top_k: int,
        lexical_hits: int,
        dense_hits: int,
        fused_candidates: int,
        below_floor_excluded: int,
        unhydrated_skipped: int,
        post_verify_dropped: int,
        score_floor: float | None,
        results: list[RetrievedChunk],
    ) -> RetrievalReport:
        return RetrievalReport(
            mode=mode,  # type: ignore[arg-type]
            lexical_only_reason=lexical_only_reason,
            outcome=outcome,  # type: ignore[arg-type]
            top_k=top_k,
            lexical_hits=lexical_hits,
            dense_hits=dense_hits,
            fused_candidates=fused_candidates,
            below_floor_excluded=below_floor_excluded,
            unhydrated_skipped=unhydrated_skipped,
            post_verify_dropped=post_verify_dropped,
            score_floor=score_floor,
            results=tuple(results),
        )
