# Product direction — from local RAG engine to a premium retrieval workbench

**Status:** proposal and execution contract. This document does not claim that planned capabilities are implemented.

## Product thesis

Turn rag-platform into a serious, local-first knowledge workbench for people who need answers they can inspect, reproduce, and trust. The product should feel like an instrument panel for evidence, not another generic chatbot.

The differentiator is not “chat with PDFs”. It is an auditable path from source document to extracted text, chunk, retrieval decision, evidence citation, and evaluation result — with explicit abstention when evidence is insufficient.

## Product principles

1. **Evidence before prose.** Show source coverage, retrieved passages, provenance, and why evidence was selected before presenting generated language.
2. **Local-first remains real.** Local storage, zero egress by default, optional generation behind explicit policy, and no surprise provider spend.
3. **Inspectable, not magical.** Every stage has a typed outcome; expose failures, fallbacks, and unmeasured states.
4. **Resource-aware by design.** Model loading and expensive work are budgeted, cancellable, and observable. Never bypass the existing RAM and disk floors.
5. **Progressive complexity.** A fast path for a first useful query; advanced controls and evaluation remain discoverable without overwhelming new users.
6. **Reproducible work.** Datasets, reports, configurations, and retrieval runs should be identifiable and comparable.

## Experience direction

A future desktop/web workbench should have four primary surfaces:

- **Library** — collections, source ingestion, document status, metadata, supported formats, and quarantine state.
- **Ask** — focused query composer, answer/abstention state, evidence rail with inline source excerpts, and retrieval diagnostics.
- **Runs** — request timeline with stage durations/outcomes, provider/fallback details, and safe error explanations.
- **Evaluate** — frozen dataset selection, dev/test safeguards, metric cards, regression comparison, and exportable reports.

Visual language: editorial technical instrument. Quiet warm-neutral canvas, deep ink typography, a single saturated accent for actions, high-contrast evidence panels, restrained motion, strong information hierarchy, and precise data visualization. Avoid generic AI gradients, excessive glassmorphism, neon dashboards, and decorative charts with no measured data.

## Release gates

### P0 — trustworthy public project (first pass)
- Clear product positioning and truthful capability matrix.
- One canonical plan pointer; historical planning and audit documents remain preserved.
- Architecture and data-flow diagrams reflect the actual committed code, not historical snapshots.
- Reproducible installation / run / test instructions and a clean contribution workflow.
- Automated CI for supported Python versions and formatting/type/lint checks where configured.
- No fabricated performance or quality claims; distinguish measured, blocked, and not-measured.

### P1 — operational completeness
- Explicit lifecycle and status endpoints for ingestion, index state, and run history without leaking secrets or absolute paths.
- Request/run correlation across API, CLI, persisted traces, and evaluation reports.
- Bounded retention and cleanup semantics for traces, temp files, and reports.
- Structured, sanitized diagnostics and actionable error categories.
- API contract tests and migration tests on clean and upgraded databases.

### P2 — product interface
- A first-party UI only after API contracts and lifecycle semantics are stable.
- Accessible navigation, keyboard workflows, responsive layouts, and empty/error/loading states.
- Evidence-first answer view, source inspection, run timeline, and evaluation comparison.
- No frontend-only mock metrics; UI reads real API data.

### P3 — advanced retrieval and generation (owner-gated)
- Real embedding model load and dense retrieval verified against the resource budget.
- Freeze a retrieval score floor only from a documented dev-set protocol; keep test split untouched until final evaluation.
- Generation remains disabled unless an owner explicitly enables it and the egress/provider policy is approved.
- Provider availability, rate limits, retries, and cost/eligibility must be explicit and bounded.

## Acceptance standard

A release is not “premium” because it has a new logo or README. It must meet all of the following:

- A new developer can set up and run the documented path without guessing.
- The full automated test suite is green on the supported environment matrix.
- Critical data-path and privacy invariants have regression tests.
- API and documentation describe the same behavior as the code.
- User-visible states are derived from actual runtime data.
- No unmeasured metric is represented as a benchmark or quality result.
- The default setup stays local-first and does not require paid services.

## Current known boundary

The repository's current README records a lexical retrieval path wired end-to-end, with dense retrieval not yet activated in production and generation disabled by default. The evaluation harness is useful but not a substitute for an operational product UI. This direction preserves those truths rather than marketing around them.
