"""Phase 1 configuration tests (plan v2.4 section 15, Phase 1 row).

All tests are offline, deterministic, and perform no file writes: settings are
constructed with explicit values (or project-relative paths that are validated,
never created). Startup-rejection behaviour is asserted as construction-time
``ValidationError`` - invalid settings mean no application object exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from rag_platform.config import PROJECT_ROOT, Settings


def _base(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {"_env_file": None}
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


# --- Defaults: retrieval-only, egress off (I3/Z4, D3 default, section 4.3) ---


def test_defaults_are_retrieval_only_and_egress_off() -> None:
    s = _base()
    assert s.egress_mode == "off"
    assert s.egress_allowlist == []
    assert s.local_generation_enabled is False
    assert s.adaptive_retrieval == "off"
    assert s.ollama_base_url == "http://localhost:11434"
    assert s.port == 8000
    assert s.log_level == "INFO"


def test_default_paths_live_under_project_root() -> None:
    s = _base()
    assert s.data_dir.is_relative_to(PROJECT_ROOT)
    assert s.reports_dir.is_relative_to(PROJECT_ROOT)


# --- Startup rejection: egress configuration must be coherent (I3/I4) ---


def test_invalid_egress_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(egress_mode="true")


def test_allowlist_mode_without_entries_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(egress_mode="allowlist")


def test_egress_off_with_allowlist_entries_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(egress_allowlist=["https://api.example.com"])


def test_non_https_allowlist_entry_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(egress_mode="allowlist", egress_allowlist=["http://api.example.com"])


def test_wildcard_allowlist_entry_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(egress_mode="allowlist", egress_allowlist=["https://*.example.com"])


def test_allowlist_entry_with_path_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(egress_mode="allowlist", egress_allowlist=["https://api.example.com/v1"])


def test_allowlist_entry_with_embedded_credentials_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(
            egress_mode="allowlist",
            egress_allowlist=["https://user:pass@api.example.com"],
        )


def test_exact_https_origins_accepted() -> None:
    s = _base(
        egress_mode="allowlist",
        egress_allowlist=["https://api.example.com", "https://api.example.com:8443"],
    )
    assert s.egress_mode == "allowlist"
    assert len(s.egress_allowlist) == 2


# --- Regression tests (Phase 1 audit correction A, 2026-10-19) ---


def test_allowlist_entry_with_backslash_in_host_rejected() -> None:
    # A backslash-laden host is not a valid RFC 3986 reg-name (parser-differential
    # hazard for the future Phase 9 exact-match boundary) - must fail closed.
    with pytest.raises(ValidationError):
        _base(
            egress_mode="allowlist",
            egress_allowlist=["https://api.example.com\\evil.com"],
        )


def test_allowlist_entry_with_space_in_host_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(
            egress_mode="allowlist",
            egress_allowlist=["https://api.example .com"],
        )


@pytest.mark.parametrize(
    "entry",
    [
        " https://api.example.com",
        "https://api.example.com ",
        "  https://api.example.com  ",
    ],
)
def test_allowlist_entry_with_surrounding_whitespace_rejected(entry: str) -> None:
    # Entries are validated verbatim and stored raw, so a padded entry must be
    # rejected outright instead of being validated as a stripped copy.
    with pytest.raises(ValidationError):
        _base(egress_mode="allowlist", egress_allowlist=[entry])


def test_allowlist_ipv6_literal_origin_accepted() -> None:
    # Correction A requirement: IPv6 literal origins stay valid, and the value
    # retained in settings is exactly the validated input.
    entry = "https://[2606:4700:4700::1111]:8443"
    s = _base(egress_mode="allowlist", egress_allowlist=[entry])
    assert s.egress_allowlist == [entry]


# --- Ollama endpoint: loopback only, any port (I3; BG-1b misconfig path) ---


def test_non_loopback_ollama_url_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(ollama_base_url="http://192.168.10.5:11434")
    with pytest.raises(ValidationError):
        _base(ollama_base_url="https://example.com:11434")


def test_ollama_url_with_path_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(ollama_base_url="http://localhost:11434/api")


def test_arbitrary_loopback_ollama_port_accepted() -> None:
    # A wrong port must be a runtime retrieval-only condition (BG-1b), not a
    # startup error, so any valid loopback port is accepted here.
    s = _base(ollama_base_url="http://127.0.0.1:11500")
    assert s.ollama_base_url == "http://127.0.0.1:11500"


# --- Z9: project paths must stay on D: (validated, never created) ---


def test_data_dir_outside_project_root_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(data_dir=Path(r"C:\Temp\rags-data"))
    with pytest.raises(ValidationError):
        _base(data_dir=Path(r"D:\other\rag-data"))


def test_reports_dir_outside_project_root_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(reports_dir=Path(r"C:\Temp\rags-reports"))


def test_relative_paths_resolve_against_project_root_not_cwd() -> None:
    s = _base(data_dir="data", reports_dir="reports")
    assert s.data_dir == (PROJECT_ROOT / "data").resolve()
    assert s.reports_dir == (PROJECT_ROOT / "reports").resolve()


# --- Strict surface: unknown keys and bad ranges fail loudly ---


def test_unknown_setting_rejected() -> None:
    with pytest.raises(ValidationError):
        _base(bogus_setting=1)


@pytest.mark.parametrize("bad_port", [0, 65536, -1])
def test_port_out_of_range_rejected(bad_port: int) -> None:
    with pytest.raises(ValidationError):
        _base(port=bad_port)


def test_adaptive_retrieval_accepts_only_on_or_off() -> None:
    assert _base(adaptive_retrieval="on").adaptive_retrieval == "on"
    with pytest.raises(ValidationError):
        _base(adaptive_retrieval="maybe")


# --- I6: no credential fields exist in settings at all ---


def test_no_credential_like_fields_in_settings() -> None:
    forbidden = ("key", "token", "secret", "password", "credential")
    for name in Settings.model_fields:
        lowered = name.lower()
        assert not any(bad in lowered for bad in forbidden), name


# --- Environment mapping (pydantic-settings; hermetic via conftest) ---


def test_environment_variables_map_to_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGRESS_MODE", "off")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11500")
    s = Settings(_env_file=None)
    assert s.egress_mode == "off"
    assert s.port == 9000
    assert s.ollama_base_url == "http://localhost:11500"


def test_environment_bool_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_GENERATION_ENABLED", "false")
    assert Settings(_env_file=None).local_generation_enabled is False
    monkeypatch.setenv("LOCAL_GENERATION_ENABLED", "true")
    assert Settings(_env_file=None).local_generation_enabled is True
