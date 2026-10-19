"""Redacted logging (plan v2.4 Phase 1; invariants I6, and I10/I15 for records).

Every log line emitted through a handler produced by :func:`make_redacting_handler`
(or wired by :func:`configure_logging`) passes through :class:`RedactingFilter`,
which deterministically scrubs secret-shaped content from the formatted message
before it reaches any sink.

Patterns covered (documented, bounded, stdlib-only - no external dependencies):

- ``Authorization: ...`` header-style values (including ``Bearer <token>``).
- ``name=value`` / ``"name": "value"`` shapes for api_key, token, secret,
  password, and relatives (case-insensitive; quote-tolerant).
- Provider-shaped key literals (``sk-...``, ``gsk_...``, ``rk-...``, ``AKIA...``,
  ``AIza...``) appearing anywhere in a message.

Known limitations (honest, Phase 1 scope):

- Exception tracebacks (``exc_info`` / ``exc_text``) are NOT redacted yet - they
  are rendered later by the formatter, outside this filter. The application must
  never log secrets in the first place (I6); this filter is defense in depth, not
  a license to log credentials.
- Only messages routed through handlers carrying this filter are scrubbed; root
  handlers configured elsewhere are outside this module's control.

Redaction is idempotent: re-processing an already-redacted message changes
nothing (``[REDACTED]`` never matches the patterns).
"""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"

# Order matters: specific "name" shapes first, then bare key/value shapes, then
# free-floating provider-shaped literals. Every pattern is a plain regex over the
# already-formatted message string.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization header: `Authorization: Bearer abc` / `authorization=abc`.
    (
        re.compile(r"(?i)\b(authorization\s*[:=]\s*)(\S+(?:[ \t]+\S+)?)"),
        r"\1" + REDACTED,
    ),
    # Standalone bearer tokens: `Bearer abc123...`.
    (
        re.compile(r"(?i)\b(bearer[ \t]+)([A-Za-z0-9._~+/=-]{8,})"),
        r"\1" + REDACTED,
    ),
    # name = value / "name": "value" shapes. Longer names first; an optional
    # closing quote after the name tolerates JSON-style logging.
    (
        re.compile(
            r"(?i)\b((?:access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|"
            r"client[_-]?secret|password|passwd|token|secret)[\"']?\s*[:=]\s*)"
            r"(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        r"\1" + REDACTED,
    ),
    # Provider-shaped key literals anywhere in a message.
    (re.compile(r"\b(?:sk|gsk|rk|AKIA)[-_][A-Za-z0-9]{6,}\b"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{8,}\b"), REDACTED),
)


def redact(text: str) -> str:
    """Return ``text`` with all secret-shaped content replaced by ``[REDACTED]``.

    Deterministic and idempotent; never raises on arbitrary input.
    """
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Scrubs the formatted message of a log record before it is emitted.

    Applied at handler level so every record routed through that handler is
    processed exactly once, after lazy ``%``-style args have been interpolated.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:
            # Never let redaction break logging itself; drop to a safe marker.
            record.msg = "<unformattable log record dropped by RedactingFilter>"
            record.args = ()
            return True
        scrubbed = redact(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = ()
        return True


def make_redacting_handler(stream: object | None = None) -> logging.Handler:
    """Build a stream handler with formatting + redaction attached (I6)."""
    if stream is None:
        handler: logging.Handler = logging.StreamHandler()
    else:
        handler = logging.StreamHandler(stream)  # type: ignore[arg-type]
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(RedactingFilter())
    handler._rag_platform_handler = True  # marker for idempotency checks
    return handler


def configure_logging(
    level: str = "INFO", stream: object | None = None
) -> logging.Logger:
    """Configure the ``rag_platform`` logger exactly once and return it.

    Idempotent: repeated calls (app factory re-entry, tests) do not stack
    handlers. Child loggers under ``rag_platform`` propagate to this logger and
    therefore inherit redaction for their records.
    """
    logger = logging.getLogger("rag_platform")
    logger.setLevel(level)
    logger.propagate = False
    already_wired = any(
        getattr(handler, "_rag_platform_handler", False) for handler in logger.handlers
    )
    if not already_wired:
        logger.addHandler(make_redacting_handler(stream))
    return logger
