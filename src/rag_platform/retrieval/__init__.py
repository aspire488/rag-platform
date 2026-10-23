"""Hybrid retrieval package (plan v2.4 Phase 5): dense + SQLite FTS5 with
reciprocal rank fusion, a post-verification stub, and the dense score
floor.

Pure library: no API, CLI, trace recorder, evaluation harness, provider
router, or generation code lives here (later phases), and nothing in this
package imports the generation layer. Importing this package loads NO
model: the embedder is injected (``Embedder`` is a structural protocol;
``MiniLMEmbedder``'s module is referenced only for its typed error family,
whose ``sentence_transformers`` import is lazy inside its loader and is
never invoked by retrieval itself).

Owner decision pending: ``Settings.retrieval_score_floor`` ships unset
(None), so the dense leg is non-operational until a numeric floor is
owner-approved; queries then run lexical-only (FTS5) with the reason
reported. See the Phase 5 report and the plan change note.
"""

from __future__ import annotations

from rag_platform.retrieval.dense import Embedder, dense_search
from rag_platform.retrieval.errors import (
    DenseIndexError,
    LexicalIndexError,
    PostVerifyError,
    QueryValidationError,
    RetrievalError,
)
from rag_platform.retrieval.fusion import RRF_K, reciprocal_rank_fusion
from rag_platform.retrieval.hybrid import DEFAULT_COLLECTION, HybridRetriever
from rag_platform.retrieval.indexer import (
    IndexReport,
    point_id_for_chunk,
    reconcile_index,
)
from rag_platform.retrieval.lexical import build_match_query, lexical_search
from rag_platform.retrieval.post_verify import PostVerifier, pass_through_post_verify
from rag_platform.retrieval.types import (
    DenseHit,
    FusedChunk,
    LexicalHit,
    RetrievedChunk,
    RetrievalReport,
)

__all__ = [
    "DEFAULT_COLLECTION",
    "DenseHit",
    "DenseIndexError",
    "Embedder",
    "FusedChunk",
    "HybridRetriever",
    "IndexReport",
    "LexicalHit",
    "LexicalIndexError",
    "PostVerifier",
    "PostVerifyError",
    "QueryValidationError",
    "RRF_K",
    "RetrievalError",
    "RetrievalReport",
    "RetrievedChunk",
    "build_match_query",
    "dense_search",
    "lexical_search",
    "pass_through_post_verify",
    "point_id_for_chunk",
    "reciprocal_rank_fusion",
    "reconcile_index",
]
