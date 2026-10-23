"""Inert-by-default local Ollama adapter (Phase 6; sections 5.3/5.1).

Design constraints honored here:

- **Lazy by construction**: no ``httpx`` client exists until the first
  ``generate`` call; constructing this adapter opens zero sockets. The
  pipeline's default path never constructs it at all (``build_client``
  returns ``None`` unless explicitly enabled + configured).
- **Loopback only (I3)**: the base URL is re-validated here (defense in
  depth on top of the Phase 1 ``Settings`` validator) - a non-loopback,
  credentialed, or path-bearing URL is rejected with a typed error.
  ``trust_env`` is disabled on the httpx client so proxy environment
  variables can never route the local socket elsewhere.
- **Frozen section-10.1 bounds**: connect timeout 15 s, total timeout
  <= 60 s per call, transient retries <= 2 (so at most 3 calls per
  request), permanent failures never retried (I15), total chain bounded
  by the request deadline. ``num_ctx`` is deliberately NOT sent - the
  model-specific context size rests on the BG-1a/D3 verdict (section
  10.3 step 8), which has not run.
- **Typed outcome mapping**: connection errors -> ``UNREACHABLE``,
  timeouts -> ``TIMEOUT``, malformed bodies -> ``MALFORMED_RESPONSE``,
  408/429/5xx -> ``PROVIDER_ERROR`` (transient), 4xx credential/payment/
  model errors -> ``PERMANENT_FAILURE`` (never retried). Failure results
  carry category labels only - never a prompt, never response content,
  never the URL.
- **No probing**: this adapter lists, downloads, loads, and benchmarks
  nothing; one POST to ``/api/generate`` is its entire surface.

Tests inject ``transport`` (``httpx.MockTransport``) or a fake ``clock``;
no test ever contacts a live server.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

from rag_platform.config import _LOOPBACK_HOSTS
from rag_platform.generation.errors import GenerationError
from rag_platform.generation.types import (
    MAX_DEADLINE_S,
    FailureCategory,
    GenerationOutcome,
    GenerationRequest,
    GenerationResult,
    ProviderInfo,
)

#: Frozen section 10.1 caps (single source of truth with types.py).
CONNECT_TIMEOUT_S = 15.0
TOTAL_TIMEOUT_S = 60.0

#: Frozen section 10.1: transient errors retried at most twice.
MAX_TRANSIENT_RETRIES = 2

#: HTTP classes (section 5.1 taxonomy): credential/payment/unsupported-
#: model style statuses are permanent - never retried, never "handled"
#: by upgrading anything (I15/Z13).
_PERMANENT_STATUSES = frozenset({400, 401, 402, 403, 404, 422})
_TRANSIENT_STATUSES = frozenset({408, 429})


def plan_timeouts(deadline_s: float, elapsed_s: float) -> httpx.Timeout | None:
    """Per-call timeout plan: clamp to the frozen caps and the deadline.

    Returns ``None`` when the remaining deadline is exhausted - the
    caller must stop without another call. ``connect`` never exceeds
    15 s and the total never exceeds the smaller of 60 s and the time
    left on the request deadline.
    """
    remaining = deadline_s - elapsed_s
    if remaining <= 0:
        return None
    total = min(remaining, TOTAL_TIMEOUT_S)
    connect = min(CONNECT_TIMEOUT_S, total)
    return httpx.Timeout(total, connect=connect)


class OllamaClient:
    """The single M1 generation adapter (section 5.3: optional fallback).

    Implements the provider-neutral ``GenerationClient`` protocol
    structurally. ``provider`` is the adapter's own identity (a fact,
    not an invented model); ``model`` comes exclusively from explicit
    configuration.
    """

    provider_name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._assert_loopback_origin(base_url)
        if not isinstance(model, str) or not model.strip():
            raise GenerationError(
                "OllamaClient requires an explicitly configured non-empty "
                "model name (section 5.1: a request never guesses a model)"
            )
        self._base_url = base_url
        self._model = model.strip()
        self._transport = transport
        self._clock = clock
        self._http: httpx.Client | None = None

    @staticmethod
    def _assert_loopback_origin(base_url: str) -> None:
        """Re-assert the I3 loopback-only rule (single source: config)."""
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https"):
            raise GenerationError("Ollama base URL must use http or https (I3)")
        if parsed.hostname not in _LOOPBACK_HOSTS:
            raise GenerationError(
                "Ollama base URL must be a loopback origin (I3: only the "
                "local socket is ever contacted)"
            )
        if parsed.username or parsed.password:
            raise GenerationError("Ollama base URL must not embed credentials (I6)")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise GenerationError("Ollama base URL must be a plain origin (I3)")

    def describe(self) -> ProviderInfo:
        """Configuration facts only (I6): no URL, no state, no probing."""
        return ProviderInfo(provider=self.provider_name, model=self._model)

    def close(self) -> None:
        """Release the lazy httpx client (idempotent)."""
        if self._http is not None:
            self._http.close()
            self._http = None

    def _http_client(self) -> httpx.Client:
        """Lazily construct the transport - nothing exists before first use."""
        if self._http is None:
            self._http = httpx.Client(
                base_url=self._base_url,
                transport=self._transport,
                # Never route the loopback socket through proxy env vars.
                trust_env=False,
            )
        return self._http

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """One bounded generate attempt chain (<= 1 + 2 calls, deadline-clamped).

        Expected provider failures come back as typed ``UNAVAILABLE``
        results; this method does not raise for network/provider trouble.
        """
        start = self._clock()
        attempts = 0
        last_transient = FailureCategory.UNREACHABLE
        for _ in range(1 + MAX_TRANSIENT_RETRIES):
            timeout = plan_timeouts(request.deadline_s, self._clock() - start)
            if timeout is None:
                return self._failed(
                    FailureCategory.TIMEOUT, attempts, self._clock() - start
                )
            attempts += 1
            kind, value = self._attempt(request, timeout)
            if kind == "answered":
                return GenerationResult(
                    provider=self.provider_name,
                    model=self._model,
                    text=value,
                    outcome=GenerationOutcome.ANSWERED,
                    failure_category=None,
                    latency_s=self._clock() - start,
                    attempts=attempts,
                )
            if kind == "permanent":
                return self._failed(
                    FailureCategory.PERMANENT_FAILURE,
                    attempts,
                    self._clock() - start,
                )
            last_transient = value  # transient category: bounded retry
        return self._failed(last_transient, attempts, self._clock() - start)

    def _attempt(
        self, request: GenerationRequest, timeout: httpx.Timeout
    ) -> tuple[str, object]:
        """One HTTP call -> (\"answered\", text) | (\"permanent\", cat) | (\"transient\", cat)."""
        payload = {
            "model": self._model,
            "prompt": request.prompt,
            "stream": False,
            "options": {"num_predict": request.max_tokens},
            # num_ctx deliberately absent: model-specific defaults wait
            # for the BG-1a/D3 verdict (section 10.3 step 8).
        }
        try:
            response = self._http_client().post(
                "/api/generate", json=payload, timeout=timeout
            )
        except httpx.TimeoutException:
            return ("transient", FailureCategory.TIMEOUT)
        except httpx.TransportError:
            return ("transient", FailureCategory.UNREACHABLE)

        status = response.status_code
        if status < 200 or status >= 300:
            if status in _PERMANENT_STATUSES:
                return ("permanent", FailureCategory.PERMANENT_FAILURE)
            if status in _TRANSIENT_STATUSES or status >= 500:
                return ("transient", FailureCategory.PROVIDER_ERROR)
            return ("permanent", FailureCategory.PERMANENT_FAILURE)

        try:
            data = response.json()
        except ValueError:
            return ("transient", FailureCategory.MALFORMED_RESPONSE)
        if not isinstance(data, dict):
            return ("transient", FailureCategory.MALFORMED_RESPONSE)
        text = data.get("response")
        if not isinstance(text, str) or not text:
            return ("transient", FailureCategory.MALFORMED_RESPONSE)
        return ("answered", text)

    def _failed(
        self, category: FailureCategory, attempts: int, latency_s: float
    ) -> GenerationResult:
        """Typed no-answer result: category + numbers only, no content."""
        return GenerationResult(
            provider=self.provider_name,
            model=self._model,
            text=None,
            outcome=GenerationOutcome.UNAVAILABLE,
            failure_category=category,
            latency_s=max(latency_s, 0.0),
            attempts=attempts,
        )
