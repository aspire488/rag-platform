"""Shared test configuration (plan v2.4 Phase 1).

Settings-related environment variables are removed for every test so that
configuration tests are hermetic: defaults are asserted against the code's own
defaults, never against ambient machine state. These tests perform no network
access, load no model, and contact no service (M1 gate 8 direction; the full
socket-instrumented egress suite expands at Phase 6).
"""

from __future__ import annotations

import pytest

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
