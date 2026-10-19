"""Local MiniLM embeddings wrapper (plan v2.4 Phase 3; section 6 "Embeddings",
section 11 ``embeddings\\`` ("MiniLM wrapper (lazy, CPU)"), section 15 row 3,
decision D2 approved 2026-10-19).

Scope and guarantees (Phase 3):

- **Provider-neutral to the rest of the project** - the application sees only
  :class:`MiniLMEmbedder` with a typed ``encode`` contract; the embedding
  library is an internal, swappable implementation detail.
- **Lazy by construction.** Importing this module (or constructing an
  embedder) never imports ``sentence_transformers`` or ``torch`` and never
  loads a model. The library import and model construction happen once,
  inside the first :meth:`MiniLMEmbedder.encode` call.
- **Zero-network by construction.** The model is constructed with
  ``local_files_only=True``, so resolution happens exclusively against the
  project-local Hugging Face hub cache; no download fallback exists. This is
  a structural guarantee (code), stronger than - and independent of - the
  ``HF_HUB_OFFLINE`` environment flags in ``scripts\\env.ps1``.
- **Path containment (Z9).** The cache directory is resolved through
  :func:`rag_platform.db.sqlite.resolve_under_project` at construction and
  must stay strictly inside ``D:\\rag-platform``; a rejected path creates
  nothing. The default cache location is
  ``<project>\\.cache\\huggingface\\hub`` - exactly ``HF_HOME\\hub`` as set
  by ``scripts\\env.ps1`` (the ``HF_HOME`` *environment variable* is
  deliberately not read: the wrapper takes its location from code, and a
  stray non-project value would be rejected by containment anyway).
- **Explicit model identity and dimension.** :data:`DEFAULT_MODEL_ID` and
  :data:`EXPECTED_DIMENSION` are fixed from the canonical plan (section 6)
  and the *local* cache artifacts (``config.json`` hidden_size,
  ``1_Pooling/config.json`` word_embedding_dimension - read as text, never by
  loading the model). The dimension contract is enforced on every encode for
  the default model.
- **CPU only.** ``device`` defaults to ``"cpu"`` (plan section 6: CPU;
  section 10.1: 300-700 MB lazy load).

Deliberately out of scope (later phases): batching policy, input-size caps,
normalization policy, retrieval integration, RRF, and any cloud/hosted
embedding path - the plan names exactly one embedding model (section 6) and
no provider choice exists. Open interface decisions are recorded in
``reports\\phase3_implementation_report.md``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

from rag_platform.config import PROJECT_ROOT
from rag_platform.db.sqlite import PathContainmentError, resolve_under_project

#: The plan's single embedding model (plan section 6; no alternative exists).
DEFAULT_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

#: Project-local hub cache: ``HF_HOME\hub`` exactly as scripts\env.ps1 sets it.
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "huggingface" / "hub"

#: Embedding dimension, derived from the local snapshot's own config files
#: (``config.json`` -> hidden_size: 384; ``1_Pooling/config.json`` ->
#: word_embedding_dimension: 384). Not invented; verified as text without
#: loading the model. Enforced on encode for :data:`DEFAULT_MODEL_ID`.
EXPECTED_DIMENSION = 384

#: From the local snapshot's ``sentence_bert_config.json`` (max_seq_length: 256).
#: Recorded for observability; truncation behaviour belongs to the model.
EXPECTED_MAX_SEQ_LENGTH = 256


class EmbeddingError(Exception):
    """Base error for Phase 3 embedding operations."""


class EmbeddingValidationError(EmbeddingError):
    """An encode input failed validation before any model was touched."""


class ModelLoadError(EmbeddingError):
    """The embedding library or the local model cache could not be loaded."""


def _hub_dirname(model_id: str) -> str:
    """Hugging Face hub cache directory name for *model_id*."""
    return "models--" + model_id.replace("/", "--")


class MiniLMEmbedder:
    """Lazy, CPU-only, zero-network embedder over the project-local cache."""

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        *,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        device: str = "cpu",
    ) -> None:
        # Deterministic validation before any state is stored (no side effects:
        # no directory is created, no library is imported, no model is loaded).
        if (
            not isinstance(model_id, str)
            or model_id != model_id.strip()
            or not model_id
        ):
            raise EmbeddingValidationError(
                f"model_id must be a non-blank string; got {model_id!r}"
            )
        if model_id.count("/") != 1 or any(
            ch in model_id for ch in ("\\", ":", " ", "\t")
        ):
            raise EmbeddingValidationError(
                "model_id must be a hub-style identifier (org/name, no path "
                f"characters); got {model_id!r}"
            )
        if not isinstance(device, str) or device != device.strip() or not device:
            raise EmbeddingValidationError(
                f"device must be a non-blank string; got {device!r}"
            )
        # Z9 containment: a cache_dir outside the project is rejected here and
        # nothing is ever created (PathContainmentError, shared with db/vectorstore).
        self._cache_dir: Path = resolve_under_project(cache_dir)
        self._model_id = model_id
        self._device = device
        self._model: object | None = None

    # --- introspection ---------------------------------------------------

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def cache_dir(self) -> Path:
        """Resolved (contained) hub cache directory on D:."""
        return self._cache_dir

    @property
    def device(self) -> str:
        return self._device

    @property
    def is_loaded(self) -> bool:
        """True only after the model has actually been constructed."""
        return self._model is not None

    def cache_present(self) -> bool:
        """Whether this model's snapshot exists in the local cache.

        Pure filesystem inspection of the hub layout - never loads the model
        and never touches the network.
        """
        snapshots = self._cache_dir / _hub_dirname(self._model_id) / "snapshots"
        if not snapshots.is_dir():
            return False
        return any(entry.is_dir() for entry in snapshots.iterdir())

    # --- lazy loading -----------------------------------------------------

    def _load_model(self) -> object:
        """Construct the model once (library import + local-only load).

        The only place ``sentence_transformers`` is ever imported. Any
        failure - missing library, missing/corrupt cache - surfaces as
        :class:`ModelLoadError` with no network fallback
        (``local_files_only=True``).
        """
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ModelLoadError(
                "sentence-transformers is not importable in this environment "
                "(no download is attempted; install is Phase 0 responsibility)"
            ) from exc
        try:
            self._model = SentenceTransformer(
                self._model_id,
                cache_folder=str(self._cache_dir),
                device=self._device,
                local_files_only=True,
            )
        except Exception as exc:  # any load failure - loud, typed, offline
            raise ModelLoadError(
                f"failed to load {self._model_id!r} from the local cache "
                f"{self._cache_dir} (local_files_only; no network fallback): {exc}"
            ) from exc
        return self._model

    # --- encoding -----------------------------------------------------------

    @staticmethod
    def _validate_texts(texts: object) -> list[str]:
        """Deterministic input contract; raises before any model interaction."""
        if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence):
            raise EmbeddingValidationError(
                "texts must be a non-empty sequence of strings "
                f"(pass a list, not a single string); got {type(texts).__name__}"
            )
        items = list(texts)
        if len(items) == 0:
            raise EmbeddingValidationError(
                "texts must contain at least one string (empty input rejected; "
                "consistent with the storage layer's empty-batch rule)"
            )
        for index, item in enumerate(items):
            if not isinstance(item, str):
                raise EmbeddingValidationError(
                    f"texts[{index}] must be a str; got {type(item).__name__}"
                )
            if not item.strip():
                raise EmbeddingValidationError(
                    f"texts[{index}] must contain non-whitespace characters"
                )
        return items

    def _validate_vectors(self, raw: object, n_inputs: int) -> list[list[float]]:
        """Output-shape contract: n rows, consistent positive dimension,
        finite numbers - and the declared dimension for the default model."""
        if hasattr(raw, "tolist"):
            raw = raw.tolist()  # numpy -> plain lists at this boundary only
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise EmbeddingError(
                f"model output must be a sequence of vectors; got {type(raw).__name__}"
            )
        rows = list(raw)
        if len(rows) != n_inputs:
            raise EmbeddingError(
                f"model returned {len(rows)} vectors for {n_inputs} inputs"
            )
        expected = EXPECTED_DIMENSION if self._model_id == DEFAULT_MODEL_ID else None
        result: list[list[float]] = []
        dimension: int | None = None
        for index, row in enumerate(rows):
            if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
                raise EmbeddingError(
                    f"model output row {index} must be a sequence of numbers"
                )
            values = list(row)
            if len(values) == 0:
                raise EmbeddingError(f"model output row {index} is empty")
            floats: list[float] = []
            for position, value in enumerate(values):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise EmbeddingError(
                        f"model output row {index}[{position}] must be a real "
                        f"number; got {value!r}"
                    )
                number = float(value)
                if not math.isfinite(number):
                    raise EmbeddingError(
                        f"model output row {index}[{position}] is not finite"
                    )
                floats.append(number)
            if dimension is None:
                dimension = len(floats)
            elif len(floats) != dimension:
                raise EmbeddingError(
                    f"model output row {index} has dimension {len(floats)}, "
                    f"expected {dimension} (inconsistent output)"
                )
            result.append(floats)
        if expected is not None and dimension != expected:
            raise EmbeddingError(
                f"model {self._model_id!r} produced dimension {dimension}, but "
                f"the declared local-cache dimension is {expected}"
            )
        return result

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed *texts*; return one 1-D vector per input, row order preserved.

        Input validation runs first (a bad input never triggers a model
        load), then the model is loaded lazily (first call only), then the
        output contract is verified. Library-specific call arguments are left
        at their documented defaults (batch size 32, no normalization
        override - the snapshot's own module stack governs); see the Phase 3
        report for the recorded interface decisions.
        """
        validated = self._validate_texts(texts)
        model = self._load_model()
        try:
            raw = model.encode(  # type: ignore[attr-defined]
                list(validated), convert_to_numpy=True
            )
        except (EmbeddingError, ModelLoadError):
            raise
        except Exception as exc:  # deterministic typed failure, no retry loop
            raise EmbeddingError(
                f"embedding generation failed for {self._model_id!r}: {exc}"
            ) from exc
        return self._validate_vectors(raw, len(validated))
