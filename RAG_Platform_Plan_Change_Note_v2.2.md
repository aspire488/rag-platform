# RAG Platform — Plan Change Note v2.2

**Date:** 2026-10-18 (evidence time 17:32 +05:30)
**Scope:** Documentation-only consistency pass on the master plan. Files created: `RAG_Platform_Master_Plan_v2.2.md` (this note's subject) and this change note. No implementation, no system changes.
**Input documents reviewed:** `RAG_Platform_Master_Plan_v2.1.md`, `RAG_Platform_Plan_Change_Note_v2.1.md`, `RAG_Platform_Master_Plan_v2.md`, `RAG_Platform_Strategy_Review.md`, `RAG_Platform_Plan_Diff.md`, `RAG_Platform_Preflight_Audit.md` (historical), `RAG_Platform_Preflight_Audit_20261018-1654.md`.

## 1. Purpose

Resolve the two outstanding internal contradictions in plan v2.1 identified by the preflight audit (`RAG_Platform_Preflight_Audit_20261018-1654.md`):

- **H-1:** BG-1 benchmark sequencing — §10.3's heading said "run once at M1 start" while M1 gate 7 requires the report as an M1 exit criterion and §15 placed BG-1 in Phase 8 (after M1) — an unrunnable sequence.
- **H-3:** Request-pipeline ordering — §4.2 listed `authz_filter` **before** `cache_lookup`, while §5's pipeline description had cache lookup **before** the authorization pre-filter — a security contradiction.

The v2.1 storage policy (final decision of 2026-10-18) is **preserved unchanged** (§2, Z9, §10.2, §12, §13/R6, §14/D4-withdrawn, Phase 0 row).

## 2. Exact changes (v2.1 → v2.2)

| # | Section | Change |
|---|---------|--------|
| 1 | Header | Document renamed/versioned to v2.2; supersession chain now v1→v2.0→v2.1→v2.2. |
| 2 | §4.2 (item 2) | Stage list unchanged; added: budget selection is part of the `retrieval` stage; trace recording/failure attribution are cross-cutting recorders, not stages; explicit list of **mandatory security gates** vs **advisory diagnostics (never gates, I11)**. |
| 3 | §5 diagram | Edges `API --> CACHE`, `API --> RET` changed to `AUTH --> CACHE`, `CACHE --> RET` so the graph matches the canonical order (cache is consulted only after authn + authz pre-filter). |
| 4 | §5 pipeline paragraph | Rewritten as the canonical normative sequence matching §4.2 exactly: `authn → server-derived identity (I2) → authorization pre-filter → cache lookup (scoped + revalidated on hit) → budget → hybrid retrieval → post_verify → context_build → generation/abstention (I12) → ledger → response`; recorder cross-cutting; explicit rule "cache lookup never precedes the authorization pre-filter" and "a cache hit can never bypass current authorization"; mandatory gates vs advisory diagnostics listed. |
| 5 | §10.3 | Split into **BG-1a — model-only baseline (Phase 0.5)** (steps 1–4, 6–8; proves the model verdict feeding D3 and Phase 6 generation defaults; evidence: dated Markdown+JSON report under `reports\` + verdict/measured numbers recorded in `docs/RESOURCE_BUDGET.md`) and **BG-1b — pipeline-unavailability proof (Phase 6)** (step 5; proves degraded retrieval-only mode end-to-end through the real pipeline; evidence: Phase 6 integration-test status in the M1 gate report; satisfies M1 gate 2). Added `blocked` (stop-condition abort) to the verdict enum so aborted runs are recorded honestly. |
| 6 | §7.1 M1 scope | References BG-1a (Phase 0.5) and BG-1b (Phase 6) instead of one BG-1. |
| 7 | §7.1 M1 gate 2 | Annotated as the BG-1b proof, executed at Phase 6. |
| 8 | §7.1 M1 gate 7 | Now the **BG-1a** report (run at Phase 0.5); verdict enum aligned with §10.3 (`usable-cpu` / `usable-gpu(partial/full)` / `too-slow` / `too-heavy` / `blocked`-with-reason); states BG-1b is verified by gate 2, not gate 7. |
| 9 | §14 D3 | "Post-BG-1**a** model verdict"; defer condition references BG-1a data. |
| 10 | §15 phase table | New row **Phase 0.5** (BG-1a, depends only on Phase 0); Phase 6 row adds BG-1b proof; Phase 8 row drops BG-1 (was the mis-sequenced placement). |
| 11 | §11 tree | `bench_model.ps1` labeled (BG-1a); v2.2 listed as current document, v2.1 marked historical. |

Chronological BG-1 sequence after this pass: **Phase 0 → BG-1a (model verdict, feeds D3 + Phase 6 defaults) → Phases 1–5 → Phase 6 (BG-1b degraded-mode proof) → Phase 8 (evals) → M1 gate check (gates 2 and 7)**. No benchmark results are claimed; nothing has been run.

## 3. Audit findings classification

- **Resolved in v2.1:** H-2 (C: ≥ 5 GB gate removed by the final storage decision; D: sufficiency ≥ 10 GB retained). Verified still absent in v2.2.
- **Resolved in v2.2 (this pass):** H-1 (BG-1 sequencing), H-3 (pipeline ordering).
- **Required before implementation, explicitly NOT resolved here (owner decision needed at D1 approval):**
  - **M-1** — operational log-sink destination on D: is not specified in the plan; setup.ps1 does not assert that `UV_CACHE_DIR`/`HF_HOME` resolve under D:.
  - **M-2** — concrete verification commands (FTS5, Qdrant load, egress) are not enumerated in the plan.
  - **M-3** — eval self-bias mitigations (frozen answer key, blind answer grading) are not required by the plan's acceptance criteria.
  - **M-4** — RAM-gate `blocked` vs `failed` semantics for tests are not fully specified. *Partially addressed:* BG-1a now has an explicit `blocked` verdict; general test-verdict semantics remain open.
  - **M-5** — Hugging Face offline flags (`HF_HUB_OFFLINE` et al.) and the offline test's tooling-egress scope are not stated.
  These are documented gaps, not silently "fixed"; folding them in is a small plan amendment or Phase 0 decision.
- **Recommended but optional:** audit C-9-style label clarifications. The §5 diagram's `(I1)/(I2)/(I3)` node labels were checked and are **Innovation 1/2/3** (Evidence Ledger / Failure Observatory / Adaptive Budget), not invariants — no change needed.
- **Intentionally deferred:** L-1 (DBHUB_DSN retarget), L-2 (opencode.jsonc coexistence), L-3 (OpenCode plugin load failures), L-4 (.ruff_cache under C:\Users\joelj\.cache), and unverified unknowns U1–U10 (each resolves during Phase 0 / BG-1a).

## 4. Verification performed

- **BG-1 grep (v2.2, 14 hits):** every reference consistent — §7.1 scope, gates 2/7, §10.3 split, scripts comment, D3, Phase 0.5/6 rows. No "M1 start" or Phase-8 BG-1 remains.
- **Pipeline/cache grep (46 hits):** every cache mention follows the authorization pre-filter; §4.2 = §5 = diagram = M2/M4 gates = I1/I11/I12; no contradictory alternative sequence remains; recorder consistently cross-cutting, nine stages preserved.
- **C:-gate grep (11 hits):** only "no C: gate" statements, the preserved D: ≥ 10 GB working minimum, and RAM (not disk) figures; no obsolete C: free-space requirement.
- **Line diff v2.1 vs v2.2:** only the 11 regions in §2 changed; nothing else.
- **v2.1 integrity:** SHA256 `7322AFE9B779A99FB368F80AC800DA71C37F59B27609FB91F3633D05444CB9EF` unchanged after creating v2.2; all historical files untouched.

## 5. Remaining unresolved issues

1. M-1 … M-5 (above) — documented as open, owner decision at D1.
2. BG-1a `too-slow` floor is provisional (4 tok/s) and needs user confirmation before BG-1a runs.
3. BG-1 evidence locations (`reports\` dated artifact + `docs/RESOURCE_BUDGET.md` entry) were not stated in v2.1; they are defined in v2.2 following existing plan conventions (gitignored `reports\` on D:, committed `docs\`).

No internal contradiction was found that could not be resolved from the existing source documents.

## 6. Boundaries observed

Documentation only. No Phase 0, no code, no setup scripts, no dependency installs, no Git init/state changes, no model downloads, no benchmarks, no tests executed, no global configuration changes, no external calls. All writes went to `D:\rag-platform`; nothing was written to `C:`. Checks performed were document searches and file comparisons only — no filesystem/benchmark/system verification is claimed. Implementation remains gated on explicit D1 approval.

**Files:**
- `D:\rag-platform\RAG_Platform_Master_Plan_v2.2.md` (new canonical plan)
- `D:\rag-platform\RAG_Platform_Plan_Change_Note_v2.2.md` (this note)
