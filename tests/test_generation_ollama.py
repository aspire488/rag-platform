"""Phase 6 Ollama adapter tests: lazy construction, loopback re-validation,
request serialization (no ``num_ctx`` - BG-1a/D3 pending), response
parsing, typed transient/permanent classification with bounded retries,
and deadline planning.

Offline and deterministic: every HTTP interaction runs through
``httpx.MockTransport`` (in-process) or an injected fake clock - no live
server, no model, no network, no DNS.
"""

from __future__ import annotations

import json

import httpx
import pytest

from rag_platform.generation.errors import GenerationError
from rag_platform.generation.ollama import (
    CONNECT_TIMEOUT_S,
    MAX_TRANSIENT_RETRIES,
    TOTAL_TIMEOUT_S,
    OllamaClient,
    plan_timeouts,
)
from rag_platform.generation.types import (
    MAX_DEADLINE_S,
    FailureCategory,
    GenerationOutcome,
    GenerationRequest,
    GenerationResult,
)

_EVIDENCE = ()  # placeholder replaced per-request in helpers below


def _request(prompt: str = "Say something about walruses.") -> GenerationRequest:
    from rag_platform.generation.types import EvidenceSpan

    return GenerationRequest(
        prompt=prompt,
        evidence=(
            EvidenceSpan(chunk_id="c" * 64, doc_id="d" * 64, char_start=0, char_end=9),
        ),
        max_tokens=128,
        deadline_s=MAX_DEADLINE_S,
    )


def _client(
    handler,
    *,
    model: str = "test-model",
    clock=None,
) -> OllamaClient:
    kwargs = {
        "base_url": "http://127.0.0.1:11434",
        "model": model,
        "transport": httpx.MockTransport(handler),
    }
    if clock is not None:
        kwargs["clock"] = clock
    return OllamaClient(**kwargs)  # type: ignore[arg-type]


# --- lazy construction and loopback enforcement --------------------------


def test_construction_is_lazy_and_describe_is_configuration_only() -> None:
    client = OllamaClient(base_url="http://localhost:11434", model="m")
    assert client._http is None  # no transport object exists yet
    info = client.describe()
    assert info.provider == "ollama"
    assert info.model == "m"
    assert client._http is None  # describe() probes nothing
    client.close()  # idempotent on an unopened client
    client.close()


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com:11434",  # not loopback (I3)
        "http://10.0.0.5:11434",  # not loopback (I3)
        "http://user:pass@localhost:11434",  # credentials (I6)
        "http://localhost:11434/api",  # not a plain origin (I3)
        "ftp://localhost:11434",  # wrong scheme (I3)
    ],
)
def test_non_compliant_base_urls_are_rejected_typed(base_url: str) -> None:
    with pytest.raises(GenerationError):
        OllamaClient(base_url=base_url, model="m")


def test_empty_model_is_rejected_never_invented() -> None:
    for model in ("", "   "):
        with pytest.raises(GenerationError):
            OllamaClient(base_url="http://localhost:11434", model=model)


# --- request serialization / response parsing ----------------------------


def test_successful_request_serialization_and_result_fields() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "The walrus tusk.", "done": True})

    client = _client(handler)
    result = client.generate(_request())
    assert result.outcome is GenerationOutcome.ANSWERED
    assert result.text == "The walrus tusk."
    assert result.provider == "ollama"
    assert result.model == "test-model"
    assert result.failure_category is None
    assert result.attempts == 1
    assert result.latency_s >= 0.0
    # Exact request shape: bounded prompt, explicit model, output cap,
    # streaming off - and NO num_ctx (model defaults wait for BG-1a/D3).
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/generate"
    body = seen["body"]
    assert body["model"] == "test-model"
    assert body["prompt"] == "Say something about walruses."
    assert body["stream"] is False
    assert body["options"] == {"num_predict": 128}
    assert "num_ctx" not in body["options"]
    client.close()


@pytest.mark.parametrize(
    ("payload", "expected_calls"),
    [
        (b"definitely not json", 1 + MAX_TRANSIENT_RETRIES),
        (b'["a", "list"]', 1 + MAX_TRANSIENT_RETRIES),
        (b"{}", 1 + MAX_TRANSIENT_RETRIES),
        (b'{"response": ""}', 1 + MAX_TRANSIENT_RETRIES),
        (b'{"response": 42}', 1 + MAX_TRANSIENT_RETRIES),
    ],
)
def test_malformed_responses_are_transient_and_bounded(
    payload: bytes, expected_calls: int
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=payload)

    client = _client(handler)
    result = client.generate(_request())
    assert result.outcome is GenerationOutcome.UNAVAILABLE
    assert result.failure_category is FailureCategory.MALFORMED_RESPONSE
    assert result.attempts == expected_calls
    assert calls == expected_calls  # retries <= 2 (frozen cap), then stop
    client.close()


def test_connection_errors_map_to_unreachable_with_bounded_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused")

    client = _client(handler)
    result = client.generate(_request())
    assert result.failure_category is FailureCategory.UNREACHABLE
    assert result.outcome is GenerationOutcome.UNAVAILABLE
    assert result.attempts == 1 + MAX_TRANSIENT_RETRIES
    assert calls == 3
    client.close()


def test_timeouts_map_to_timeout_with_bounded_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("read timed out")

    client = _client(handler)
    result = client.generate(_request())
    assert result.failure_category is FailureCategory.TIMEOUT
    assert result.attempts == 1 + MAX_TRANSIENT_RETRIES
    client.close()


def test_transient_failure_then_success_succeeds_within_retry_budget() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("flaky first hop")
        return httpx.Response(200, json={"response": "recovered"})

    client = _client(handler)
    result = client.generate(_request())
    assert result.outcome is GenerationOutcome.ANSWERED
    assert result.text == "recovered"
    assert result.attempts == 2
    assert calls == 2
    client.close()


@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_permanent_statuses_are_never_retried(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": "nope"})

    client = _client(handler)
    result = client.generate(_request())
    assert result.outcome is GenerationOutcome.UNAVAILABLE
    assert result.failure_category is FailureCategory.PERMANENT_FAILURE
    assert result.attempts == 1  # permanent: exactly one call, no retry
    assert calls == 1
    client.close()


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_statuses_retry_then_report_provider_error(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": "busy"})

    client = _client(handler)
    result = client.generate(_request())
    assert result.failure_category is FailureCategory.PROVIDER_ERROR
    assert result.attempts == 1 + MAX_TRANSIENT_RETRIES
    assert calls == 3
    client.close()


def test_failure_results_never_carry_the_prompt(
    sentinel: str = "SENTINEL_PROMPT_XYZ",
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.ConnectError("refused")

    client = _client(handler)
    result = client.generate(_request(prompt=f"Leak me: {sentinel}"))
    rendered = repr(result)
    assert sentinel not in rendered
    assert result.text is None  # category + numbers only
    client.close()


# --- deadline planning ----------------------------------------------------


def test_plan_timeouts_clamps_to_frozen_caps_and_remaining_deadline() -> None:
    fresh = plan_timeouts(MAX_DEADLINE_S, 0.0)
    assert fresh is not None
    assert fresh.connect <= CONNECT_TIMEOUT_S
    assert fresh.read <= TOTAL_TIMEOUT_S
    partial = plan_timeouts(MAX_DEADLINE_S, 59.5)
    assert partial is not None
    assert partial.read <= 0.5  # never outlives the deadline
    assert plan_timeouts(MAX_DEADLINE_S, 60.0) is None  # exhausted -> stop
    assert plan_timeouts(MAX_DEADLINE_S, 999.0) is None
    shorter = plan_timeouts(30.0, 0.0)
    assert shorter is not None and shorter.read <= 30.0


def test_exhausted_deadline_stops_before_any_call() -> None:
    # Fake clock: start at 0, immediately past the 60 s deadline.
    ticks = iter([0.0, 61.0, 61.0, 61.0, 61.0])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"response": "never reached"})

    client = _client(handler, clock=lambda: next(ticks))
    result = client.generate(_request())
    assert result.failure_category is FailureCategory.TIMEOUT
    assert result.attempts == 0  # no call was made
    assert calls == 0
    client.close()


def test_adapter_identity_and_result_are_frozen_contracts() -> None:
    client = _client(lambda request: httpx.Response(200, json={"response": "hi"}))
    result = client.generate(_request())
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        result.text = "mutated"  # type: ignore[misc]
    assert isinstance(result, GenerationResult)
    client.close()
