"""Shared test configuration (plan v2.4 Phases 1-2).

Settings-related environment variables are removed for every test so that
configuration tests are hermetic: defaults are asserted against the code's own
defaults, never against ambient machine state. These tests perform no network
access, load no model, and contact no service (M1 gate 8 direction; the full
socket-instrumented egress suite expands at Phase 6).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from rag_platform.config import PROJECT_ROOT

_SETTINGS_ENV_KEYS = (
    "EGRESS_MODE",
    "EGRESS_ALLOWLIST",
    "OLLAMA_BASE_URL",
    "PORT",
    "LOCAL_GENERATION_ENABLED",
    "ADAPTIVE_RETRIEVAL",
    "LOG_LEVEL",
    "DATA_DIR",
    "REPORTS_DIR",
)


@pytest.fixture(autouse=True)
def _settings_env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _SETTINGS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def storage_tmp() -> Iterator[Path]:
    """Isolated storage directory for Phase 2 tests - always on D:.

    Always created under ``<project>/data/tmp`` (gitignored; plan section
    11). Each test receives its own unique directory, and teardown removes
    only that directory - never a sibling, never anything outside the
    project root.
    """
    base = PROJECT_ROOT / "data" / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"phase2-{uuid4().hex}"
    path.mkdir()
    if not path.is_relative_to(PROJECT_ROOT):
        raise AssertionError(f"storage_tmp escaped the project root: {path}")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
