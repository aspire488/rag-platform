# RAG Platform — Plan Change Note v2.3 (Final Pre-Implementation Close-Out)

**Date:** 2026-10-18 (evidence time ~17:58 +05:30)
**Scope:** Documentation-only final close-out. Files created: `RAG_Platform_Master_Plan_v2.3.md` and this note. No implementation, no system changes, nothing executed.
**Input documents reviewed:** `RAG_Platform_Master_Plan_v2.2.md`, `RAG_Platform_Plan_Change_Note_v2.2.md`, v2.1/v2.0 plans and change notes, strategy review, plan diff, both preflight audits.

## 1. Purpose

Make `RAG_Platform_Master_Plan_v2.3.md` the implementation-ready final candidate: (a) correct the §5 architecture diagram so it no longer conflicts with the normative pipeline; (b) close out audit findings M-1…M-5 in documentation; (c) settle the BG-1a `too-slow` threshold honestly as an owner decision; (d) preserve every approved non-negotiable.

## 2. Exact changes (v2.2 → v2.3)

| # | Section | Change |
|---|---------|--------|
| 1 | Header | Versioned to v2.3; supersession chain extended; v2.2 marked preserved. |
| 2 | §5 component overview | Labeled explicitly as **wiring only** ("Component overview"; ordering truth lives in the request-flow diagram + §4.2). `RET` node no longer bundles pre/post-filters (now "hybrid search only"); new nodes **PVERIFY** (post-retrieval authorization verify — mandatory gate) and **CTX** (bounded context build). Edges fixed: `BUDGET --> RET` (was `RET --> BUDGET`) and `RET --> PVERIFY --> CTX --> GEN` (was `RET --> GEN`, which bypassed both gates). Diagram node labels `(I1)/(I2)/(I3)` disambiguated to `(Innovation 1/2/3)` so they cannot be misread as invariants I1–I3 (I3 = the egress guard). Same fix in the §11 tree comment. |
| 3 | §5 new request-flow diagram | Added an accurate 11-step flow (`authn ★ → identity ★ → authz pre-filter ★ → cache lookup → budget → retrieval → post-verify ★ → context → generation/abstention ★ → ledger → response`) with gates marked ★, the egress guard shown as the path outbound calls traverse, and trace/audit as a **cross-cutting recorders subgraph** outside the request path. Caption states: gates cannot be bypassed/reordered/delayed/overridden by cache hits, budget decisions, advisory diagnostics, client flags, or recorders (I11/I12); recorders observe, never sit inside the flow. |
| 4 | §5 prose + §4.2 | Pipeline paragraph and stage list now carry an explicit mapping: the **eleven flow steps map onto the nine recorded trace stages** (identity within `authn`, budget selection within `retrieval`), so diagram, prose, and trace schema agree exactly. |
| 5 | §9.1 item 5 (M-4) | Frozen **status semantics** for `passed / failed / skipped / blocked / not-measured`, applying identically to metric rows, tests, benchmark verdicts, gate items, and DoD items. Safety stops, unavailable prerequisites, and user aborts are always `blocked` — never `passed`, never auto-`failed` — and require re-run or explicit recorded waiver before gate approval. Old item 5 renumbered 6. |
| 6 | §9.2 + §9.5 (M-3) | Eval safeguards: fixture authorship documented with the author–evaluator risk disclosed (R19); corpus/labels/answer keys/dev-test split SHA256-frozen **before the first tuning run**; ground truth never regenerated from system output; thresholds tune on `dev` only; `test` evaluated once per candidate config with the test-evaluation count reported; approach stays local/zero-cost/proportionate. |
| 7 | §9.3 + §12 (M-5) | HF offline configuration specified **project-locally** (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1` set by `scripts\env.ps1`, process-scoped; the test harness also sets them in-process before importing `sentence_transformers`; no global/user env changes). Egress test honesty: the suite + eval run observe application-process sockets (any non-loopback `connect()` fails them); they cannot prove absence of other-process traffic or unexecuted paths; **offline flags suppress download attempts by design but are not proof of zero traffic — socket instrumentation is** (§9.3 row extended). |
| 8 | §12 + §11 tree (M-1) | All operational paths named and pinned to D:: logs → `data\logs\`, traces → `data\traces\`, temp (`TMP`/`TEMP`) → `data\tmp\`, caches (`UV_CACHE_DIR`, `HF_HOME`) → `.cache\uv\`, `.cache\huggingface\`, databases → `data\sqlite\`, `data\qdrant\`, reports → `reports\` (tree updated with `logs\ tmp\`). `setup.ps1` must run **destination-aware resolved-path assertions** (every resolved path starts under `D:\rag-platform` and is writable, fail loud) **before any project artifact is created**. Unavoidable C: writes: disclose exact path + reason and stop for explicit approval. No C: free-space threshold of any kind. §10.2 cross-references §12. Phase 0 row now includes the path assertions. |
| 9 | §12 verification table (M-2) | Concrete Windows/PowerShell commands for FTS5 availability, Ollama reachability, D:-path assertions, egress-default config, Qdrant local persistence + payload filtering, and the socket-instrumented egress suite — each typed as static / requires-Phase-0-deps / requires-implementation, with earliest phase. Stated plainly: **none executed during planning; no results claimed.** |
| 10 | §10.3 + §14 (BG-1 threshold) | The 4 tok/s `too-slow` floor is now marked **provisional recommendation, unapproved, requiring the owner's D12 confirmation before BG-1a runs**; `blocked` clarified as a safety abort, not a model verdict (§9.1 semantics); BG-1a's prerequisites now include the recorded D12 decision. New **D12** row in §14 with default recommendation (confirm 4 tok/s) and approval point (before Phase 0.5). |
| 11 | §16 DoD item 1 | `blocked` items must be re-run or explicitly waived (reason recorded) per §9.1 — never treated as a pass. |

## 3. Audit-finding dispositions

- **M-1, M-2, M-3, M-4, M-5:** resolved **in documentation** (details above). They remain honestly open at the implementation level: setup.ps1 must actually assert the paths, the harness must actually set the flags and instrument sockets, and reports must actually use the status semantics — otherwise they become implementation gaps, not plan gaps.
- **BG-1 `too-slow` threshold:** **not resolved** — correctly retained as explicit owner decision **D12** with a default recommendation (4 tok/s provisional) and the exact approval point (before Phase 0.5). No preference was invented and nothing was silently approved.
- H-1/H-3 (v2.2) and H-2 (v2.1) remain resolved; no new contradictions were found.

## 4. Preserved non-negotiables (verified unchanged)

D:-only project storage; no C: free-space gate (grep confirms only "no C: gate" statements); D: ≥ 10 GB working minimum; C: tools/models read-executed in place, never relocated; no global env/config changes; no Docker, paid services, cloud dependencies, or new model downloads; `EGRESS_MODE=off` default; Evidence Ledger, Failure Observatory, bounded Adaptive Retrieval Budget, evaluation-first approach, conditional GraphRAG experiment; BG-1a/BG-1b phase split; authorization-before-cache ordering. All historical plans, change notes, audits, review, and diff unchanged (SHA256-verified: v1 `7E739904…`, v2 `AF4114BD…`, v2.1 `7322AFE9…`, v2.2 `2E4ED16C…`, notes `3E1E5453…`/`1A18A9EF…`, review `6E3D9347…`, diff `69BF45D2…`, audits `9371C2F5…`/`805EF7C5…`).

## 5. Verification performed

- Diagram edge/node grep: `BUDGET --> RET`, `RET --> PVERIFY --> CTX --> GEN` present; no `--> BUDGET` edge, no `RET --> GEN`, no old "pre-filter -> hybrid search -> post-verify" RET label remains; both mermaid blocks read back and structurally sound.
- Ordering grep across all cache/pipeline/trace references: consistent with the normative eleven-step flow and nine recorded stages.
- D12/status-semantics grep: defined in §14/§9.1, referenced consistently in §10.3, BG-1a prerequisites, and DoD.
- C:-gate grep (12 hits): zero obsolete free-space requirements; only "no C: gate" statements, the D: 10 GB minimum, and RAM figures.
- Line diff v2.2 vs v2.3: only the 17 intended regions changed.
- Integrity: all ten historical files carry original hashes and timestamps; nothing written to C:.

## 6. Remaining open items (all explicit, none hidden)

1. **Owner decisions:** D1–D3, D5–D12 (D4 closed/withdrawn). D12 must be recorded before BG-1a; D1 gates all implementation.
2. Implementation-level delivery of the M-1…M-5 closures (assertions, flags, instrumentation, status semantics) — verified at Phase 0/gates.
3. Unverified environment unknowns (U-series from the audit) resolve during Phase 0 and BG-1a with measured evidence.

## 7. Boundaries observed

Documentation only. No Phase 0, code, scripts run, installs, Git, downloads, benchmarks, tests, global config changes, or external calls; no verification command in §12 was executed; all writes on `D:\rag-platform`. Implementation begins only after explicit D1 approval.

**Files:**
- `D:\rag-platform\RAG_Platform_Master_Plan_v2.3.md` (final candidate plan)
- `D:\rag-platform\RAG_Platform_Plan_Change_Note_v2.3.md` (this note)
