"""FastAPI application factory - /health skeleton (plan v2.4 Phase 1).

Phase 1 scope and honest boundaries:

- Settings are validated first: ``create_app`` refuses to build an application
  from invalid settings (startup rejection; tests/test_config.py and
  tests/test_health.py cover both directions).
- The app performs **no network access**: creating it and serving ``/health``
  never contacts Ollama, any cloud provider, or any other host (I3/Z4). There is
  no generation code in this phase at all - provider routing is future, gated
  work (v2.4 section 5.1, D16) and is deliberately not present.
- ``/health`` reports configuration facts only (I6): mode labels and booleans.
  It never echoes URLs, filesystem paths, or any value that could disclose
  configuration internals - asserted by tests.
- OpenAPI/docs endpoints are disabled (product scope: API + CLI, plan section 3).

Start (later phase, documented here for discoverability; not run by tests)::

    $env:PYTHONPATH="src"; python -m uvicorn --factory rag_platform.main:create_app
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from . import __version__
from .config import Settings, settings_summary
from .logging_setup import configure_logging

logger = logging.getLogger("rag_platform")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Raises ``pydantic.ValidationError`` if ``settings`` is invalid, or if the
    ambient environment/``.env`` produces invalid settings when ``settings`` is
    omitted - i.e. bad configuration stops the application before it exists.
    """
    resolved = settings if settings is not None else Settings()
    configure_logging(resolved.log_level)

    app = FastAPI(
        title="rag-platform",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = resolved

    @app.get("/health")
    def health() -> dict[str, object]:
        """Configuration-only health summary; never leaks values (I6)."""
        summary: dict[str, object] = {
            "status": "ok",
            "service": "rag-platform",
            "version": __version__,
        }
        summary.update(settings_summary(app.state.settings))
        return summary

    logger.info(
        "application created (egress_mode=%s, local_generation_enabled=%s)",
        resolved.egress_mode,
        resolved.local_generation_enabled,
    )
    return app
