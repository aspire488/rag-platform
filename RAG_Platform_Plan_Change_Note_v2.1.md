# RAG Platform — Plan Change Note (v2.0 → v2.1)

**Document:** `RAG_Platform_Plan_Change_Note_v2.1.md` — 2026-10-18
**Applies:** final storage decision ("D:-only project, no C: dependency")
**Corrected plan:** `RAG_Platform_Master_Plan_v2.1.md` (new versioned file; `RAG_Platform_Master_Plan_v2.md` v2.0 preserved untouched, SHA256 `AF4114BD…486E5`)
**Scope:** documentation-only correction. No Phase 0, no code, no dependencies, no Git, no models, no global settings changed. No architecture or milestone redesign — seven surgical edits, all inside v2.

---

## Exact edits (v2.0 → v2.1)

| # | Section | v2.0 wording (essence) | v2.1 wording (essence) | Why |
|---|---|---|---|---|
| 1 | Header (lines 3–8) | "version 2.0"; supersedes v1 | "version 2.1"; supersedes v2.0; new **Storage policy (final decision)** paragraph: D: is the workspace and destination for all project-generated data; C: is not a storage destination and its free space is not a readiness gate; existing C: tools may be read/executed, never relocated | Version the corrected plan; state the decision at the top |
| 2 | §2 **Z9** | "All new artifacts on D: … C: must not grow." | Adds: logs, reports, Git metadata/worktrees, temp files, project interpreters enumerated; "C: free space is **not** a readiness gate — setup checks gate on D: destination sufficiency"; existing C: installations read/execute-only; no global installs | Make the D:-only rule exhaustive and remove any implied C: gate |
| 3 | §10.2 Disk strategy | "setup script asserts C: free space and fails loud" | Destination-aware checks: D: writable + sufficient for the planned operation (fail loud with observed value); free-space snapshot recorded (D: primary, C: informational); **no C: free-space minimum**; unavoidable C:-writing tool limitation → disclose + stop for approval. D4 note updated (models stay on C:) | Remove the C: gate; replace with destination checks; disclosure path added |
| 4 | §12 `setup.ps1` bullet | "asserts: C: free space ≥ 5 GB (fail loud), D: writable, …" | Destination-aware asserts: **D:** free space ≥ 10 GB working minimum before scaffold/venv (fail loud, observed value recorded) + D: writable; snapshot in setup report; **"There is no C: free-space gate"**; unavoidable C: requirement → disclose and stop for approval | The core removal (C: ≥ 5 GB). Destination-disk safety retained/strengthened on D: |
| 5 | §13 **R6** mitigation | "C: overflow risk; C: free-space assert (v1 wording carried)" | Mitigation note updated: all project writes pinned to D:; setup gates on D: sufficiency, not a C: minimum | Keep the risk register consistent with the new guard |
| 6 | §14 **D4** | "Optional: relocate `OLLAMA_MODELS` (9.2 GB) C: → D: (system env change)" | **Withdrawn** — existing installations/model stores are never relocated; models stay on C:, read-only use | Final decision forbids relocating existing model stores; decision list must not offer it |
| 7 | §15 Phase 0 row + §16 DoD item 7 | "C:-space assert"; "C: did not grow … free-space deltas" | "destination-aware D:-space check (no C: gate)"; DoD 7 reworded as storage-policy verification ("no project activity wrote to C:" snapshots), explicitly "not a setup gate" | Cross-reference cleanup; DoD evidence retained as verification, not gate |

Also updated: directory tree in §11 (v2.0 marked historical, v2.1 marked "this document").

## What was deliberately NOT changed

- Architecture, milestones M1–M4, invariants I1–I13, evaluation methodology, BG-1 protocol (stop conditions, no-C:-dependency already held), zero-cost rules Z1–Z8/Z10–Z12, risks R15–R22, decisions D1–D3/D5–D11.
- All original documents preserved: `RAG_Platform_Master_Plan.md` (v1), `RAG_Platform_Master_Plan_v2.md` (v2.0), `RAG_Platform_Strategy_Review.md`, `RAG_Platform_Plan_Diff.md`, `RAG_Platform_Preflight_Audit.md`, `RAG_Platform_Preflight_Audit_20261018-1654.md`.

## Note on the prior audit's H-2 finding

The preflight audit's H-2 proposed *keeping* the C: ≥ 5 GB threshold with `blocked`-semantics. The final decision supersedes that recommendation by removing the C: gate entirely; destination (D:) sufficiency checks and the disclosure-and-approval path for unavoidable C: tool operations replace it. No other audit finding is affected (H-1 BG-1 timing, H-3 pipeline ordering, M-1…M-5 remain as documented recommendations for a future correction pass; they are out of scope for this storage-only change).

*End of change note.*
