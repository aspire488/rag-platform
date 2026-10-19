"""Phase 1 redacted-logging tests (plan v2.4; invariants I6/I15).

Deterministic, offline, stdlib-only. Each test builds its own capture logger or
capture stream so tests never depend on global logging state or ordering.
"""

from __future__ import annotations

import io
import logging

from rag_platform.logging_setup import (
    REDACTED,
    RedactingFilter,
    configure_logging,
    make_redacting_handler,
    redact,
)


def _capture_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    capture = logging.getLogger(name)
    capture.handlers.clear()
    capture.propagate = False
    capture.setLevel(logging.DEBUG)
    capture.addHandler(make_redacting_handler(stream))
    return capture, stream


# --- Pure redact() behaviour ---


def test_authorization_header_value_redacted() -> None:
    out = redact("Authorization: Bearer sekrit-value-123")
    assert "sekrit-value-123" not in out
    assert REDACTED in out
    assert out.startswith("Authorization:")


def test_api_key_assignment_redacted() -> None:
    out = redact("using api_key=sk-abc123456789 for the request")
    assert "sk-abc123456789" not in out
    assert REDACTED in out


def test_json_style_token_redacted() -> None:
    out = redact('{"token": "ghp_secret12345xyz"} received')
    assert "ghp_secret12345xyz" not in out
    assert REDACTED in out


def test_provider_shaped_literals_redacted_anywhere() -> None:
    out = redact("keys in use: gsk_abcdefgh12345 and AIzaSyDummyKey1234567")
    assert "gsk_abcdefgh12345" not in out
    assert "AIzaSyDummyKey1234567" not in out
    assert out.count(REDACTED) == 2


def test_ordinary_message_untouched() -> None:
    message = "retrieval finished chunks=8 top_score=0.81 duration_ms=42"
    assert redact(message) == message


def test_redact_is_idempotent() -> None:
    once = redact("Authorization: Bearer anothersecret999")
    assert redact(once) == once


# --- Handler/filter behaviour on real log records ---


def test_filter_scrubs_records_with_interpolated_args() -> None:
    capture, stream = _capture_logger("rag_platform.tests.redaction.args")
    capture.warning("Authorization: %s", "Bearer abc123def456")
    output = stream.getvalue()
    assert "abc123def456" not in output
    assert REDACTED in output


def test_filter_leaves_normal_records_intact() -> None:
    capture, stream = _capture_logger("rag_platform.tests.redaction.normal")
    capture.info("query served top_k=8 mode=retrieval-only")
    output = stream.getvalue()
    assert "query served top_k=8 mode=retrieval-only" in output
    assert REDACTED not in output


def test_filter_survives_unformattable_records() -> None:
    # A broken %-format must not raise inside logging.
    record = logging.LogRecord(
        name="rag_platform.tests.redaction.broken",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="bad format %s %s",
        args=("only-one-arg",),
        exc_info=None,
    )
    filt = RedactingFilter()
    assert filt.filter(record) is True


# --- configure_logging wiring ---


def test_configure_logging_is_idempotent() -> None:
    target = logging.getLogger("rag_platform")
    saved = list(target.handlers)
    saved_level = target.level
    saved_propagate = target.propagate
    target.handlers = []
    try:
        configure_logging("INFO", stream=io.StringIO())
        configure_logging("WARNING", stream=io.StringIO())
        marked = [
            h for h in target.handlers if getattr(h, "_rag_platform_handler", False)
        ]
        assert len(marked) == 1
        assert target.level == logging.WARNING
        assert target.propagate is False
    finally:
        target.handlers = saved
        target.level = saved_level
        target.propagate = saved_propagate
