"""Phase 5 configuration tests: retrieval bounds and the unset score floor.

Startup-rejection conventions (Phase 1): invalid values raise
``pydantic.ValidationError`` before any application object exists. The
score floor ships unset (None) - no numeric default is invented; a value,
if set, must lie in the dense score space and is validated as such.
Hermetic: environment is cleaned per test by conftest.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from rag_platform.config import Settings


def _settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {"_env_file": None}
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def test_retrieval_defaults_match_the_plan() -> None:
    s = _settings()
    assert s.retrieval_top_k == 8  # plan 10.1: default 8
    assert s.retrieval_score_floor is None  # deliberately unset pending owner
    assert s.retrieval_query_max_chars == 512
    assert s.retrieval_dense_candidates == 20
    assert s.retrieval_lexical_candidates == 20


@pytest.mark.parametrize("bad", [0, -1, 21, 100])
def test_top_k_outside_1_to_20_is_rejected_at_startup(bad: object) -> None:
    # (bools are lax-coerced to int by pydantic like every other int field
    # here; the retrieval runtime layers reject them - see hybrid/lexical.)
    with pytest.raises(ValidationError):
        _settings(retrieval_top_k=bad)


@pytest.mark.parametrize("good", [1, 8, 20])
def test_top_k_bounds_are_accepted(good: int) -> None:
    assert _settings(retrieval_top_k=good).retrieval_top_k == good


@pytest.mark.parametrize("bad", [1.5, -1.5, float("nan"), float("inf"), -float("inf")])
def test_score_floor_must_be_a_finite_cosine_similarity(bad: float) -> None:
    with pytest.raises(ValidationError):
        _settings(retrieval_score_floor=bad)


def test_score_floor_accepts_none_and_in_range_values() -> None:
    assert _settings(retrieval_score_floor=None).retrieval_score_floor is None
    assert _settings(retrieval_score_floor=0.3).retrieval_score_floor == 0.3
    assert _settings(retrieval_score_floor=-1.0).retrieval_score_floor == -1.0
    assert _settings(retrieval_score_floor=1.0).retrieval_score_floor == 1.0


@pytest.mark.parametrize("bad", [0, -5, 2001])
def test_query_char_cap_is_bounded(bad: int) -> None:
    with pytest.raises(ValidationError):
        _settings(retrieval_query_max_chars=bad)


@pytest.mark.parametrize(
    "field", ["retrieval_dense_candidates", "retrieval_lexical_candidates"]
)
@pytest.mark.parametrize("bad", [0, 101])
def test_candidate_counts_are_bounded(field: str, bad: int) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: bad})


def test_nan_floor_rejection_is_explicit() -> None:
    # Guard the guard: pydantic would otherwise happily coerce "nan".
    assert not math.isfinite(float("nan"))
    with pytest.raises(ValidationError):
        _settings(retrieval_score_floor=math.nan)


def test_environment_mapping_for_retrieval_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RETRIEVAL_TOP_K", "12")
    monkeypatch.setenv("RETRIEVAL_QUERY_MAX_CHARS", "256")
    monkeypatch.setenv("RETRIEVAL_DENSE_CANDIDATES", "40")
    monkeypatch.setenv("RETRIEVAL_LEXICAL_CANDIDATES", "33")
    monkeypatch.setenv("RETRIEVAL_SCORE_FLOOR", "0.4")
    s = _settings()
    assert s.retrieval_top_k == 12
    assert s.retrieval_query_max_chars == 256
    assert s.retrieval_dense_candidates == 40
    assert s.retrieval_lexical_candidates == 33
    assert s.retrieval_score_floor == 0.4


def test_invalid_env_value_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_TOP_K", "21")
    with pytest.raises(ValidationError):
        _settings()
