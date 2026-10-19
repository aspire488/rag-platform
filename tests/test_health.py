"""Phase 1 /health skeleton tests (plan v2.4 section 15; invariants I6, I3).

In-process ASGI test client only: no server process, no port binding, no network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rag_platform.config import Settings
from rag_platform.main import create_app

_HEALTH_KEYS = {
    "status",
    "service",
    "version",
    "egress_mode",
    "egress_allowlist_configured",
    "local_generation_enabled",
    "paths_on_project",
}


def _settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {"_env_file": None}
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def test_health_ok_and_contract() -> None:
    client = TestClient(create_app(_settings()))
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == _HEALTH_KEYS
    assert body["status"] == "ok"
    assert body["service"] == "rag-platform"
    assert body["egress_mode"] == "off"
    assert body["egress_allowlist_configured"] is False
    assert body["local_generation_enabled"] is False
    assert body["paths_on_project"] is True


def test_health_does_not_echo_configuration_values() -> None:
    client = TestClient(create_app(_settings()))
    text = client.get("/health").text
    assert "://" not in text  # no URLs (Ollama endpoint etc.)
    assert "11434" not in text  # no internal ports
    assert "\\" not in text  # no filesystem paths


def test_health_reflects_configured_flags_without_leaking_entries() -> None:
    settings = _settings(
        local_generation_enabled=True,
        egress_mode="allowlist",
        egress_allowlist=["https://api.example.com"],
    )
    body = TestClient(create_app(settings)).get("/health").json()
    assert body["local_generation_enabled"] is True
    assert body["egress_allowlist_configured"] is True
    assert body["egress_mode"] == "allowlist"
    # The entry itself must never appear in the response.
    assert "api.example.com" not in repr(body)


def test_docs_and_openapi_disabled() -> None:
    client = TestClient(create_app(_settings()))
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_unknown_route_is_404() -> None:
    client = TestClient(create_app(_settings()))
    assert client.get("/query").status_code == 404


def test_invalid_settings_reject_app_creation() -> None:
    # Startup rejection: the allowlist mode without entries must fail before an
    # application object exists.
    with pytest.raises(ValidationError):
        create_app(_settings(egress_mode="allowlist"))
