"""Post-retrieval verification hook (plan v2.4 Phase 5: post-verify stub).

The canonical pipeline order (plan section 5) places an INDEPENDENT
post-retrieval verification between retrieval and everything downstream.
Phase 5 ships the stage as an honest, typed pass-through so the ordering
exists, is injectable, and is testable today; the real two-phase
authorization re-verify is M2 work and is NOT implemented here.

Honesty contract (documented and tested): the default verifier performs no
authorization, no filtering, and provides NO security guarantee. It is not
a security control and must never be labeled or counted as one (I11: the
stage exists for the future gate; claiming the guarantee before M2 exists
would violate Z8 honest reporting).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from rag_platform.retrieval.types import RetrievedChunk

#: The typed hook: results in, results out. The M2 implementation will
#: additionally need the authenticated principal from the surrounding
#: request context; the signature stays results-first so the pass-through
#: and filter stubs need nothing else.
PostVerifier = Callable[[Sequence[RetrievedChunk]], Sequence[RetrievedChunk]]


def pass_through_post_verify(
    results: Sequence[RetrievedChunk],
) -> list[RetrievedChunk]:
    """Default Phase 5 verifier: returns *results* unchanged, in order.

    Provides no authorization guarantee (M2 implements the real
    independent re-verify); exists so the canonical stage ordering holds
    and can be asserted by tests now.
    """
    return list(results)
