"""Embeddings package (plan v2.4 Phase 3): lazy, CPU-only, zero-network
MiniLM wrapper and the D2-approved local cache copy/verification helpers.

Importing this package is side-effect free with respect to the embedding
library: ``sentence_transformers``/``torch`` are imported only inside the
first :meth:`~rag_platform.embeddings.minilm.MiniLMEmbedder.encode` call.
"""

from __future__ import annotations

from rag_platform.embeddings.cache import (
    CacheCopyError,
    CacheCopyReport,
    copy_model_cache,
    verify_cache_copy,
)
from rag_platform.embeddings.minilm import (
    DEFAULT_CACHE_DIR,
    DEFAULT_MODEL_ID,
    EXPECTED_DIMENSION,
    EXPECTED_MAX_SEQ_LENGTH,
    EmbeddingError,
    EmbeddingValidationError,
    MiniLMEmbedder,
    ModelLoadError,
)

__all__ = [
    "DEFAULT_CACHE_DIR",
    "DEFAULT_MODEL_ID",
    "EXPECTED_DIMENSION",
    "EXPECTED_MAX_SEQ_LENGTH",
    "CacheCopyError",
    "CacheCopyReport",
    "EmbeddingError",
    "EmbeddingValidationError",
    "MiniLMEmbedder",
    "ModelLoadError",
    "copy_model_cache",
    "verify_cache_copy",
]
