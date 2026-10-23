"""FastAPI application factory - /health (Phase 1) and /query (Phase 7).

Honest boundaries, by phase:

- Settings are validated first: ``create_app`` refuses to build an application
  from invalid settings (startup rejection; tests/test_config.py and
  tests/test_health.py cover both directions).
- ``/health`` reports configuration facts only (I6): mode labels and booleans.
  It never echoes URLs, filesystem paths, or any value that could disclose
  configuration internals - asserted by tests. It opens no database, probes no
  provider, constructs no client, and loads no model.
- ``/query`` (POST, Phase 7) is a thin surface over the shared
  :func:`rag_platform.api.run_query` orchestration: lexical-only retrieval +
  the Phase 6 generation pipeline + the best-effort boundary trace. The
  default configuration answers retrieval-only with zero socket activity;
  generation runs only when owner-enabled. The application DB is opened
  lazily per query (never at import, construction, or ``/health``).
- Error envelope (I6): ``{"error": {"category", "detail"}}`` with constant,
  sanitized detail strings - request field values, prompts, retrieved
  content, paths, and exception messages are never echoed. Unexpected
  errors log the exception class name only.
- OpenAPI/docs endpoints are disabled (product scope: API + CLI, plan section 3).

Start (documented for discoverability; not run by tests)::

    $env:PYTHONPATH="src"; python -m uvicorn --factory rag_platform.main:create_app
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .api import (
    MAX_QUERY_BODY_BYTES,
    ApiError,
    QueryRequest,
    QueryResponse,
    default_db_path,
    run_query,
    translate_query_error,
)
from .config import Settings, settings_summary
from .logging_setup import configure_logging

logger = logging.getLogger("rag_platform")


def create_app(
    settings: Settings | None = None, *, db_path: Path | None = None
) -> FastAPI:
    """Build the application.

    Raises ``pydantic.ValidationError`` if ``settings`` is invalid, or if the
    ambient environment/``.env`` produces invalid settings when ``settings``
    is omitted - i.e. bad configuration stops the application before it exists.

    *db_path* defaults to ``<data_dir>/sqlite/rag.db`` (contained, on D:);
    tests inject an isolated path. Nothing is opened here - the database is
    opened per query, lazily.
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
    app.state.db_path = db_path if db_path is not None else default_db_path(resolved)

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

    @app.post("/query")
    def query(payload: QueryRequest, response: Response) -> QueryResponse:
        """Answer one bounded query: generated+cited, abstained, or
        retrieval-only - always the canonical Phase 6 response shape."""
        try:
            run = run_query(
                app.state.settings, payload.query, db_path=app.state.db_path
            )
        except Exception as exc:  # mapped to a safe envelope below
            raise translate_query_error(exc) from None
        # Server-derived correlation id (I2): echoed, never client-supplied.
        response.headers["X-Request-ID"] = run.request_id
        return QueryResponse.from_run(run)

    @app.middleware("http")
    async def bound_query_body(request: Request, call_next):  # noqa: ANN202
        """Reject oversized /query bodies BEFORE parsing (I7)."""
        if request.method == "POST" and request.url.path == "/query":
            raw_length = request.headers.get("content-length")
            if raw_length is not None:
                try:
                    declared = int(raw_length)
                except ValueError:
                    declared = -1
                if declared > MAX_QUERY_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "category": "request_too_large",
                                "detail": "request body exceeds the bounded size "
                                f"limit of {MAX_QUERY_BODY_BYTES} bytes",
                            }
                        },
                    )
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(status_code=exc.status_code, content=exc.as_body())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request,
        exc: RequestValidationError,  # noqa: ARG001
    ) -> JSONResponse:
        # Never echo field values (they include the raw prompt - I6).
        logger.warning("request validation failed (%d errors)", len(exc.errors()))
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "category": "invalid_request",
                    "detail": "request validation failed; field values are "
                    "never echoed",
                }
            },
        )

    @app.exception_handler(Exception)
    async def _internal_error(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        # Class name only: never the message, never the traceback (I6).
        logger.warning("unhandled application error (class=%s)", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "category": "internal_error",
                    "detail": "unexpected internal error",
                }
            },
        )

    logger.info(
        "application created (egress_mode=%s, local_generation_enabled=%s)",
        resolved.egress_mode,
        resolved.local_generation_enabled,
    )
    return app
