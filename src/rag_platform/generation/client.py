"""Provider-neutral generation client seam (Phase 6; section 5.1).

The orchestration layer speaks only this ``Protocol``: providers implement
it, the pipeline never speaks a provider SDK dialect (section 6: no new
dependency, no provider SDK - the single M1 adapter lives in
``ollama.py`` over the already-installed ``httpx``).

``build_client`` is the ONLY selection point and is deliberately stingy:
with the shipped defaults (``local_generation_enabled=False``, or no
explicit ``generation_model``) it returns ``None`` and constructs
nothing capable of a socket (Z3/Z11/I3 - default state is retrieval-only
with zero network activity). A request never guesses a model: an absent
model means an absent client, never a default model name (section 5.1).

Phase 9's router (gated D16) will replace the factory body with
sequential failover over this same ``Protocol`` - the seam is already in
place; nothing in Phase 6 implements failover, attempt caps across
providers, or any cloud path.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from rag_platform.config import Settings
from rag_platform.generation.types import (
    GenerationRequest,
    GenerationResult,
    ProviderInfo,
)


@runtime_checkable
class GenerationClient(Protocol):
    """The provider-neutral contract (section 5.1 response shape)."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Produce one result or one typed no-answer outcome.

        Expected failures (unreachable, timeout, malformed, permanent)
        are returned as typed ``UNAVAILABLE`` results, never raised;
        raising indicates a contract bug and is handled by the pipeline
        as ``CLIENT_ERROR``.
        """
        ...

    def describe(self) -> ProviderInfo:
        """Configuration facts for this client (booleans/labels only, I6)."""
        ...


def build_client(settings: Settings) -> GenerationClient | None:
    """Construct the single M1 adapter when - and only when - enabled.

    Returns ``None`` without constructing anything unless local
    generation is explicitly enabled AND a model is explicitly
    configured. The constructed client still opens no connection at
    construction time (lazy ``httpx`` transport in ``ollama.py``).
    """
    if not settings.local_generation_enabled or settings.generation_model is None:
        return None
    from rag_platform.generation.ollama import OllamaClient

    return OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.generation_model,
    )
