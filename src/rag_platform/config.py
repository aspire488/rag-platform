"""Deterministic configuration validation (plan v2.4 Phase 1).

Construction of :class:`Settings` performs all validation: invalid settings raise
``pydantic.ValidationError`` before any application object exists, which is the
Phase 1 "startup-rejection" behaviour (plan v2.4 section 15, Phase 1 row).

Security-relevant rules implemented here:

- I3 / Z4: ``EGRESS_MODE`` defaults to ``off``. With egress off the allowlist must
  be empty; a half-configured state is rejected loudly instead of being ignored.
- I4: enabling egress (``EGRESS_MODE=allowlist``) requires at least one exact
  ``https://`` origin: exact host, no wildcards, no path/query/fragment, no
  embedded credentials. Anything weaker fails validation.
- I3: the Ollama base URL must be a loopback origin (localhost / 127.0.0.1 / ::1);
  configuration can never point the app at a non-local host. An arbitrary loopback
  *port* is deliberately allowed so a misconfigured port can be exercised at
  runtime (plan BG-1b unavailability proof) - a wrong port is a runtime
  ``retrieval-only`` condition, not a startup error.
- Z9: project data paths must resolve inside the project root (D:\\rag-platform);
  a path outside the project (e.g. on C:) is rejected at startup. Relative paths
  resolve against the project root, never the current working directory.
- I6: this module contains no credential fields at all. Provider settings are a
  future, separately gated concern (plan v2.4 D16) and are deliberately absent,
  so no configuration value here can be a secret.
- Plan defaults preserved: local generation disabled (D3 default / v2.4 section
  5.3), ``ADAPTIVE_RETRIEVAL=off`` (v2.4 section 4.3 kill switch).

Construction is pure validation: no network access, no service calls, no file
writes (paths are validated, not created).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Project root derived from this file's location: <root>/src/rag_platform/config.py.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _validate_port(port: int, what: str) -> int:
    if not 1 <= port <= 65535:
        raise ValueError(f"{what} must be in 1..65535, got {port}")
    return port


def _validate_allowlist_entry(entry: str) -> None:
    """One allowlist entry must be an exact ``https://host[:port]`` origin (I4).

    The entry is validated verbatim - no normalization or stripping is applied -
    so the value settings retain is exactly the value that was checked
    (fail-closed; Phase 1 audit correction A, 2026-10-19).
    """
    if entry != entry.strip():
        raise ValueError(
            f"allowlist entry {entry!r} must not have leading or trailing "
            "whitespace; entries are stored verbatim and validated exactly (I4)"
        )
    if not entry:
        raise ValueError("egress allowlist entries must be non-empty exact origins")
    parsed = urlparse(entry)
    if parsed.scheme != "https":
        raise ValueError(f"allowlist entry {entry!r} must use https (I4: TLS required)")
    host = parsed.hostname
    if not host:
        raise ValueError(f"allowlist entry {entry!r} has no host")
    if "*" in host:
        raise ValueError(
            f"allowlist entry {entry!r} must be an exact host; wildcards are never accepted (I4)"
        )
    if any(ch.isspace() or ch == "\\" for ch in host):
        raise ValueError(
            f"allowlist entry {entry!r} host must contain neither whitespace nor "
            "backslashes (exact RFC 3986 origin, I4)"
        )
    if parsed.username or parsed.password:
        raise ValueError(f"allowlist entry {entry!r} must not embed credentials (I6)")
    if parsed.path.rstrip("/") != "" or parsed.query or parsed.fragment:
        raise ValueError(
            f"allowlist entry {entry!r} must be scheme://host[:port] only (exact-host allowlist, I4)"
        )
    if parsed.port is not None:
        _validate_port(parsed.port, f"allowlist entry {entry!r} port")


class Settings(BaseSettings):
    """Application settings with fail-loud validation.

    Environment mapping (case-insensitive, pydantic-settings): ``EGRESS_MODE``,
    ``EGRESS_ALLOWLIST`` (JSON list), ``OLLAMA_BASE_URL``, ``PORT``,
    ``LOCAL_GENERATION_ENABLED``, ``ADAPTIVE_RETRIEVAL``, ``LOG_LEVEL``,
    ``DATA_DIR``, ``REPORTS_DIR``. A project-root ``.env`` file is read when
    present (``.env`` is gitignored; ``.env.example`` documents the keys).
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="forbid",
    )

    # Egress guard (I3/I4, Z4): off is the default and the only shipped state.
    egress_mode: Literal["off", "allowlist"] = "off"
    egress_allowlist: list[str] = Field(default_factory=list)

    # Local endpoint only (I3); loopback enforced by validator below.
    ollama_base_url: str = "http://localhost:11434"

    # API port for the future server (Phase 7); validated range here.
    port: int = 8000

    # Plan defaults: generation disabled (D3 / v2.4 5.3), adaptive budget off (4.3).
    local_generation_enabled: bool = False
    adaptive_retrieval: Literal["on", "off"] = "off"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Project-local artifact paths (Z9): must stay under the project root on D:.
    data_dir: Path = PROJECT_ROOT / "data"
    reports_dir: Path = PROJECT_ROOT / "reports"

    @field_validator("ollama_base_url")
    @classmethod
    def _ollama_must_be_loopback_origin(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("ollama_base_url must use http or https")
        host = parsed.hostname
        if host not in _LOOPBACK_HOSTS:
            raise ValueError(
                "ollama_base_url must be loopback (localhost, 127.0.0.1 or ::1); "
                f"got host {host!r} (I3: only the local socket is ever contacted)"
            )
        if parsed.username or parsed.password:
            raise ValueError("ollama_base_url must not embed credentials (I6)")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError(
                "ollama_base_url must be a plain origin (no path, query or fragment)"
            )
        if parsed.port is not None:
            _validate_port(parsed.port, "ollama_base_url port")
        return value

    @field_validator("port")
    @classmethod
    def _port_range(cls, value: int) -> int:
        return _validate_port(value, "port")

    @field_validator("data_dir", "reports_dir")
    @classmethod
    def _paths_must_stay_on_project(cls, value: Path, info: ValidationInfo) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        resolved = path.resolve()
        root = PROJECT_ROOT.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise ValueError(
                f"{info.field_name} must resolve under the project root {root} "
                "(Z9: all project artifacts stay on D:)"
            )
        return resolved

    @model_validator(mode="after")
    def _egress_configuration_must_be_coherent(self) -> "Settings":
        if self.egress_mode == "off":
            if self.egress_allowlist:
                raise ValueError(
                    "egress_mode='off' requires an empty egress_allowlist (I3/Z4): "
                    "allowlist entries present while egress is disabled - fix the "
                    "configuration instead of leaving it ambiguous"
                )
        else:  # egress_mode == "allowlist"
            if not self.egress_allowlist:
                raise ValueError(
                    "egress_mode='allowlist' requires at least one exact https "
                    "allowlist entry (I4); egress stays fully off until enablement "
                    "is completely configured and owner-approved"
                )
            for entry in self.egress_allowlist:
                _validate_allowlist_entry(entry)
        return self


def settings_summary(settings: Settings) -> dict[str, object]:
    """Sanitized configuration summary for /health (I6).

    Booleans and mode labels only - never URLs, paths, or any value that could
    echo configuration internals. ``configured`` reports presence of state, not
    its content.
    """
    return {
        "egress_mode": settings.egress_mode,
        "egress_allowlist_configured": bool(settings.egress_allowlist),
        "local_generation_enabled": settings.local_generation_enabled,
        "paths_on_project": (
            settings.data_dir.is_relative_to(PROJECT_ROOT)
            and settings.reports_dir.is_relative_to(PROJECT_ROOT)
        ),
    }
