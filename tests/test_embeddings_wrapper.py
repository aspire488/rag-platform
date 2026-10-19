"""Phase 3 embeddings-wrapper tests (plan v2.4 section 15 row 3).

Contract coverage with synthetic data only: no real model is loaded, no
network is touched, and ``sentence_transformers``/``torch`` are never
actually imported (the missing-library path is simulated by poisoning the
import, not by uninstalling anything).
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rag_platform.config import PROJECT_ROOT
from rag_platform.db.sqlite import PathContainmentError
from rag_platform.embeddings import (
    DEFAULT_MODEL_ID,
    EXPECTED_DIMENSION,
    EmbeddingError,
    EmbeddingValidationError,
    MiniLMEmbedder,
    ModelLoadError,
)


class _FakeModel:
    """Deterministic stand-in for SentenceTransformer.encode (synthetic only)."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.last_kwargs: dict[str, object] = {}

    def encode(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        self.last_kwargs = kwargs
        return [
            [float((len(text) + step) % 97) for step in range(self.dim)]
            for text in texts
        ]


def _forbidden_load(self: MiniLMEmbedder) -> object:  # noqa: ARG001
    raise AssertionError("model loading must not be reached for invalid input")


# --- laziness ------------------------------------------------------------


def test_package_and_constructor_are_lazy() -> None:
    """Importing the package and constructing an embedder loads no library
    and creates no directory (checked in a fresh interpreter)."""
    probe = (
        "import sys; import rag_platform.embeddings as e; "
        "m = e.MiniLMEmbedder('test/probe', cache_dir=r'__CACHE__'); "
        "bad = ('sentence_transformers' in sys.modules) or ('torch' in sys.modules); "
        "print('EAGER' if bad else 'LAZY_OK'); "
        "print('LOADED' if m.is_loaded else 'NOT_LOADED'); "
        "print('DIR_CREATED' if (__import__('pathlib').Path(r'__CACHE__').exists()) else 'NO_DIR');"
    )
    cache_probe = PROJECT_ROOT / "data" / "tmp" / "phase3-lazy-probe"
    code = probe.replace("__CACHE__", str(cache_probe))
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")},
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "LAZY_OK" in result.stdout, result.stdout
    assert "NOT_LOADED" in result.stdout, result.stdout
    assert "NO_DIR" in result.stdout, result.stdout
    assert not cache_probe.exists()


# --- constructor validation ---------------------------------------------


def test_constructor_validation_and_containment(storage_tmp: Path) -> None:
    good = MiniLMEmbedder(cache_dir=storage_tmp / "hub")
    assert good.is_loaded is False
    assert good.model_id == DEFAULT_MODEL_ID
    assert good.device == "cpu"
    assert good.cache_dir == (storage_tmp / "hub").resolve()
    assert not (storage_tmp / "hub").exists()  # constructor has no side effects

    for bad_id in (
        "",
        "  ",
        " leading/slash",
        "no-slash",
        "a/b/c",
        "a\\b",
        "C:/x/y",
        7,
    ):
        with pytest.raises(EmbeddingValidationError):
            MiniLMEmbedder(bad_id, cache_dir=storage_tmp / "hub")  # type: ignore[arg-type]

    with pytest.raises(EmbeddingValidationError):
        MiniLMEmbedder("test/model", cache_dir=storage_tmp / "hub", device="")

    escaped = Path("C:/Windows/Temp/phase3_embedder_must_not_exist")
    with pytest.raises(PathContainmentError):
        MiniLMEmbedder(cache_dir=escaped)
    assert not escaped.exists()


# --- input validation before any load -----------------------------------


@pytest.mark.parametrize(
    "bad_texts",
    [
        [],
        None,
        "single string",
        b"bytes",
        [1, 2],
        [""],
        ["   "],
        [("a", "b")],
        ["ok", None],
    ],
)
def test_encode_rejects_bad_inputs_before_loading(
    storage_tmp: Path, bad_texts: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = MiniLMEmbedder("test/fake", cache_dir=storage_tmp / "hub")
    # The loader raises if reached: validation must come first.
    monkeypatch.setattr(MiniLMEmbedder, "_load_model", _forbidden_load)
    with pytest.raises(EmbeddingValidationError):
        embedder.encode(bad_texts)  # type: ignore[arg-type]
    assert embedder.is_loaded is False


# --- output contract with a synthetic model -----------------------------


def test_encode_output_contract_with_fake_model(storage_tmp: Path) -> None:
    embedder = MiniLMEmbedder("test/fake", cache_dir=storage_tmp / "hub")
    fake = _FakeModel(dim=8)
    embedder._model = fake  # seam: no library import, no model load

    texts = ["alpha", "beta gamma", "delta"]
    first = embedder.encode(texts)
    assert embedder.is_loaded is True
    assert len(first) == len(texts)
    assert all(len(row) == 8 for row in first)
    assert all(
        isinstance(value, float) and math.isfinite(value)
        for row in first
        for value in row
    )
    assert fake.last_kwargs.get("convert_to_numpy") is True

    # determinism: identical input -> identical output
    assert embedder.encode(texts) == first
    # row order is input order (distinct inputs -> distinct synthetic rows)
    assert first[0] != first[1]


def test_encode_enforces_declared_dimension_for_default_model(
    storage_tmp: Path,
) -> None:
    embedder = MiniLMEmbedder(cache_dir=storage_tmp / "hub")  # default model id
    embedder._model = _FakeModel(dim=8)
    with pytest.raises(EmbeddingError, match="dimension"):
        embedder.encode(["hello world"])

    embedder_ok = MiniLMEmbedder(cache_dir=storage_tmp / "hub")
    embedder_ok._model = _FakeModel(dim=EXPECTED_DIMENSION)
    rows = embedder_ok.encode(["hello world"])
    assert len(rows[0]) == EXPECTED_DIMENSION


def test_encode_wraps_model_failures_as_typed_error(storage_tmp: Path) -> None:
    class _ExplodingModel:
        def encode(self, texts: list[str], **kwargs: object) -> object:
            raise RuntimeError("synthetic failure")

    embedder = MiniLMEmbedder("test/fake", cache_dir=storage_tmp / "hub")
    embedder._model = _ExplodingModel()
    with pytest.raises(EmbeddingError, match="embedding generation failed"):
        embedder.encode(["hello"])


# --- missing library / cache behaviour ----------------------------------


def test_missing_library_fails_loudly_without_network(
    storage_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = MiniLMEmbedder("test/fake", cache_dir=storage_tmp / "hub")
    # Simulate an uninstalled library without touching the environment:
    # a None entry in sys.modules makes the import fail deterministically.
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(ModelLoadError, match="not importable"):
        embedder.encode(["hello"])
    assert embedder.is_loaded is False


def test_cache_present_reflects_hub_layout(storage_tmp: Path) -> None:
    hub = storage_tmp / "hub"
    assert MiniLMEmbedder("test/fake", cache_dir=hub).cache_present() is False

    (hub / "models--test--fake" / "snapshots" / "rev1").mkdir(parents=True)
    assert MiniLMEmbedder("test/fake", cache_dir=hub).cache_present() is True
    # a different model id is not satisfied by another model's snapshot
    assert MiniLMEmbedder("test/other", cache_dir=hub).cache_present() is False

    empty = storage_tmp / "hub_empty"
    (empty / "models--test--fake" / "snapshots").mkdir(parents=True)
    assert MiniLMEmbedder("test/fake", cache_dir=empty).cache_present() is False
