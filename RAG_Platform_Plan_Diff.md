# RAG Platform — Plan Diff (v1 → v2)

**Project root:** `D:\rag-platform`
**Document:** `RAG_Platform_Plan_Diff.md` — version 1.0, 2026-10-18
**Compares:** `RAG_Platform_Master_Plan.md` (v1.0, SHA256 `7E739904BBB46B3A57CAB94012DF767BEA3C495F53B02560F9AD057052EC9FDD` at review time — **preserved untouched**) against `RAG_Platform_Master_Plan_v2.md` (v2.0).
**Evidence base:** `RAG_Platform_Strategy_Review.md`.
**Status:** Planning only. No implementation, Git, packages, downloads, services, or system changes were performed.

---

## 1. Summary of the Change

v1 was a **generic permission-aware RAG platform** plan: build the full stack (baseline RAG → multi-tenancy → cache + quota routing → GraphRAG) with strict zero-cost and resource discipline. v2 keeps every constraint and most substrate decisions, but **re-points the project** after a build-vs-reuse review: established platforms already provide chat-over-documents, so v2 spends its complexity budget on what none of them ships locally and zero-cost — a **claim-level Evidence Ledger**, a **deterministic Failure Observatory**, and a **hard-budgeted adaptive retrieval policy** — all sitting on a **first-class evaluation foundation** that lands in M1 instead of being one item among many.

**One-line characterization:** v1 optimizes for *having all the features*; v2 optimizes for *three measurable differentiators plus honest measurement*, with table-stakes features kept thin and GraphRAG demoted to a conditional experiment.

---

## 2. What Was Preserved (unchanged in force)

| Area | Preserved items |
|---|---|
| **Zero-cost rules** | All Z1–Z11 carried verbatim; Z12 added (planning docs never deleted/overwritten) |
| **Environment facts** | Hardware, disk, Ollama/models, MiniLM cache, torch CPU-only, Docker-stopped, license verifications, Qdrant-local persistence evidence, GitHub-Models-retired finding (v1 §2.1) |
| **Assumptions discipline** | A1–A10 carried (aliases, partial offload, mmap, free-tier drift, pypdf, Python 3.14 compat, etc.) |
| **Security invariants** | I1–I9 verbatim; I10–I13 added for new components (diagnostic scoping, advisory tiers never gate, abstention unsuppressable, ledger honesty) |
| **Adversarial tests** | T-ADV-01…13 matrix preserved; extended for ledger/trace redaction |
| **Substrate tech** | Python+uv, FastAPI+CLI, modular monolith, Ollama local, MiniLM CPU, Qdrant local mode on D:, SQLite WAL, argparse, pytest, httpx, pypdf, psutil; no Docker; no Redis/Neo4j/PostgreSQL/rerankers/dashboard/OCR/agents (escalation via Appendix C) |
| **Storage policy** | Everything new on D:; project-local env only; MiniLM cache copy; `OLLAMA_MODELS` untouched absent approval; Docker vhdx untouched; C:-space assert in setup |
| **Retrieval-only degradation** | Z11 and its M1 gate (works with Ollama stopped) |
| **Eval honesty** | Frozen datasets + SHA256, deterministic primary path, status column (`passed/failed/skipped/blocked/not-measured`), lexical-metric limitations, no fabricated results, local judge advisory-only |
| **Model strategy** | Benchmark `qwen3:8b` first; installed fallback `qwen2.5-coder:7b`; **no downloads without demonstrated need + separate approval** |
| **Cloud appendix** | Groq/Gemini/OpenRouter documented as opt-in-only with Oct-2026 verified limits and privacy caveats; never in acceptance paths |
| **Risk carry-over** | R1–R14 carried; new risks R15–R22 added |
| **Provisional budgets** | v1 §6.1 estimates carried as *provisional targets* (§10.1 v2), explicitly awaiting BG-1/measurement |

## 3. What Changed and Why

| # | Change (v1 → v2) | Reason | Evidence |
|---|---|---|---|
| C1 | **Project identity:** "permission-aware RAG platform" → "local-first RAG engine with evaluation & diagnostic core" | Strategy review found chat-platform space saturated (AnythingLLM MIT/Docker-multi-user; RAGFlow Docker+50 GB with open-source permission model "not yet supported"; Dify restricted multi-tenant license; Onyx document permissions Enterprise-only; Open WebUI additive-permission logic + 2026 RAG-authorization CVEs) — differentiation lies in evidence/diagnostics, not another chat stack | Review §2–§3, §5 |
| C2 | **Evaluation moved into M1** as a co-equal deliverable with the engine (frozen corpus, harness, trace recorder, planted difficulty structures) | Reassessment §6: "evaluation must be a first-class component… before adding advanced features"; also removes v1's risk of late eval discovery | Reassessment §6; v2 R19 mitigation |
| C3 | **New milestone M3 — Evidence Ledger + Failure Observatory** (the two selected differentiators get their own milestone) | v1 had no claim-level support classification or failure attribution; both scored highest in candidate evaluation and are the defensible gaps | Review §4 (A=34, B=35), v2 §4.1–4.2 |
| C4 | **M3 (v1) "quota-aware routing" replaced** by M4 "Adaptive Retrieval Budget + Semantic Cache" | Cloud quota-routing is dormant under `EGRESS_MODE=off`; the real local resource problem is retrieval-depth/latency/RAM — addressed by a deterministic, kill-switchable policy | Review §4 (C=27), v2 §4.3 |
| C5 | **M4 (v1) GraphRAG demoted** from milestone commitment to **conditional D10 experiment**, restricted to **LLM-free construction** (regex entities + co-occurrence + NetworkX) | Measured literature: LLM-based graph construction costs 177K–655K tokens and 77–710 s even on paper hardware; GraphRAG methods showed *lower* context relevance than vanilla RAG in one ICLR 2026 study; "curves cross around 10M tokens" — infeasible/inefficient at our scale; LinearRAG (ICLR 2026) proves the LLM-free variant exists | Review §2.3 |
| C6 | **Hybrid retrieval added:** SQLite FTS5 (BM25) + dense, RRF fusion, flag-guarded | Zero new services; "BM25 wins at scale" evidence at small-corpus scale; honestly classified as *existing, established* functionality (not a differentiator) | Review §4 (I), arXiv 2607.26497 |
| C7 | **BG-1 safe benchmark protocol** codified (§10.3 v2): pre-checks, load/sustained/unload tests, `ollama ps` evidence capture, explicit stop conditions (free RAM < 2 GB, swap, sluggishness, >15 min) | Reassessment §8: "safe benchmark protocol… stop condition if memory pressure threatens responsiveness"; also converts v1 assumptions A1–A3 into a decision procedure feeding D3 | Reassessment §8; v2 §10.3 |
| C8 | **Innovation kill switches & failure conditions** required for every selected feature (ledger degrades to citations+abstention; attribution degrades to trace-only; adaptive reverts to fixed-k) | Reassessment §4 selection rules; prevents five-superficial-features failure mode | Reassessment §4; v2 §4 failure conditions |
| C9 | **Advisory tiers formalized (I11, D9):** model-based classifiers (claim decomposition, local judge) temperature=0, off-by-default, never gating | Keeps determinism honest; prevents judge-dependence creep that the review flagged in Ragas/TruLens/DeepEval | Review §2.2; v2 §8 I11 |
| C10 | **Security invariants extended** for new surfaces: I10 (traces/ledger authorization-scoped), I12 (abstention unsuppressable), I13 (ledger never implies calibrated probabilities); §9.3 "what tests prove/cannot prove" table added | New components create new leak/overclaim surfaces; reassessment §7 demanded per-test proof limits | Reassessment §7; v2 §8 |
| C11 | **Corpus design upgraded:** frozen synthetic corpus now *plants* difficulty structures (ranking traps, contradictions, unanswerable, unsupported-claim targets, injection docs, multi-hop chains) with expected-outcome labels | Enables ledger/attribution/conflict/abstention metrics with deterministic gold; each innovation needs a measurable baseline | Reassessment §6; v2 §9.2 |
| C12 | **Candidate E (Permission-Safe Memory) rejected; full contradiction engine rejected; counterfactual probes deferred** | E: revocation-correct derived memory is research-scale security complexity vs modest value (score 19/40). Full D: needs NLI model (violates Z6) or hosted judge (violates Z2). G: valuable but 2× generation cost — recorded as post-v2 experiment | Review §4 (E=19, D=23), v2 §4.4–4.5 |
| C13 | **Docker stance hardened in prose:** "no Docker invocation anywhere; no WSL usage" in setup strategy | v1 implied it; v2 states it as a testable setup constraint given Docker's C: vhdx and stopped daemon | v2 §12 |
| C14 | **New risks R15–R22** (claim-splitter quality, containment weakness, attribution brittleness, adaptive no-gain, author-bias corpus, FTS5 absence, trace growth, advisory creep) | Each new component's realistic failure mode gets a named mitigation and fallback | v2 §13 |

## 4. What Was Added (net-new)

| Item | Where | Purpose |
|---|---|---|
| Evidence Ledger (claims→spans, support classes, conflict flags, abstention objects) | v2 §4.1 | Core differentiator 1 |
| Failure Observatory (nine-stage traces, deterministic attribution rules, planted fixtures, `report` CLI with before/after) | v2 §4.2 | Core differentiator 2 |
| Adaptive Retrieval Budget (three deterministic tiers, ≤2 retrieval calls, kill switch) | v2 §4.3 | Core differentiator 3 |
| Deterministic contradiction subset (declared-date metadata, regex conflict detector) | v2 §4.4 | Absorbed minimal slice of Candidate D |
| Ingestion-time injection scan + quarantine labels (advisory, never authorization) | v2 M1 scope, I8 | Hardening; Open WebUI CVE-2026-44554 shows RAG poisoning is a real shipped-code threat |
| Provisional metric margins + D11 re-freeze decision | v2 §4, §14 | Makes gates concrete without fabricating baselines |
| Advisory-tier rules I11 + D9 | v2 §8, §14 | Determinism honesty |
| BG-1 stop conditions | v2 §10.3 | Laptop-safety during the one authorized benchmark |

## 5. What Was Removed or Demoted

| Item | Fate | Reason |
|---|---|---|
| Quota-aware **cloud** routing (v1 M3) | **Removed from milestones** | Dormant under egress-off; cloud stays documentation-only (Z10). Local resource-awareness replaces it |
| GraphRAG as milestone commitment (v1 M4) | **Demoted** to conditional D10 experiment, LLM-free only | §3 C5 evidence |
| "Routing across multiple models" framing | **Removed** — only one generation model exists; router honesty preserved (I5, /health lists installed models only) | Never pretend multiple models exist (v1 M3 rule kept, cloud version dropped) |
| v1 M1 item "cloud free-key smoke test" (`scripts/test-keys.ps1`) | **Removed** | No keys exist, none are needed; contradicts "cloud never in acceptance path" clarity |
| Full semantic contradiction/temporal engine | **Rejected** (documented, not silently dropped) | NLI/LLM dependency violates Z6/Z2 at our scale |
| Permission-Safe Memory (Candidate E) | **Rejected** (documented) | Complexity/security-risk vs value |
| Ragas / TruLens / DeepEval as eval dependencies | **Not adopted** | Judge-centric; our deterministic harness is smaller and gate-safe (reviewed, decision recorded) |

## 6. Deferred (not rejected — recorded for later)

| Item | Trigger to reconsider |
|---|---|
| Counterfactual probes (review candidate G: remove top chunk → answer unchanged ⇒ parametric-memory suspicion) | After M3 stable; costs 2× generation per probed query; natural post-v2 experiment |
| Query-reformulation step in adaptive policy | Only with measured multi-hop deficit that expansion alone can't close; would need its own latency/RAM budget justification |
| NLI-model support classification (research-grade entailment) | Separate download approval (Z6) + written accuracy justification vs deterministic tiers (Appendix C addendum) |
| Real-document eval set from the user | User provides documents → becomes additional frozen set with own ID |
| Merging/embedding cache relocation of `OLLAMA_MODELS` | D4 approval (system env change; not blocking) |

## 7. Milestone Mapping (v1 → v2)

| v1 | v2 | Net effect |
|---|---|---|
| M1 Baseline RAG (+ eval harness as one item) | **M1 Engine + Evaluation Foundation** (eval co-equal; +FTS5, injection scan, abstention path, trace recorder, BG-1) | Broadened: eval promoted, safety benchmark inserted |
| M2 Permission-aware multi-tenancy | **M2 Permission-Aware Multi-Tenancy** (unchanged scope; ledger/trace authorization-scoping added to invariant list) | Largely preserved |
| M3 Semantic cache + quota-aware routing | **M3 Evidence Ledger + Failure Observatory** | Replaced by differentiators; cache/routing move to M4 in local form |
| M4 Lightweight GraphRAG | **M4 Adaptive Budget + Semantic Cache (+ conditional graph experiment)** | Graph demoted to conditional, LLM-free, measured experiment |
| — | D3 decision now fed by BG-1 verdict | Model strategy becomes evidence-driven before any download talk |

## 8. Requirements That Cannot Currently Be Verified

| Item | Why unverifiable now | When verified |
|---|---|---|
| All provisional metric margins (80% unsupported-claim recall, ≤10% false-flag, ≥90% abstention, ≥85% attribution, ≤2% F1 delta, 4 tok/s floor) | Baseline numbers don't exist until the harness runs | M1 baseline → D11 re-freeze → M3/M4 gates |
| `qwen3:8b` usability (A1–A5) | Benchmarks forbidden during planning | BG-1 at M1 start |
| FTS5 availability in CPython 3.14's bundled sqlite3 (R20) | Requires executing Python against the target venv | Phase 0 detection |
| Python 3.14 dependency compatibility (R7) | Requires actual install | Phase 0 |
| Whether hybrid ≥ dense-only on our corpus (M1 gate 5) | Requires frozen corpus + measurements | M1 |
| Ledger/attribution behavior on out-of-distribution text | Out of scope by design; only frozen-corpus behavior is claimed | Documented limitation, never "verified" |
| Competitor feature absences ("not found in docs") | Documentation review only, no local runs of competitors | Acknowledged in review §1; not claimed as proof |
| Open WebUI / Ragas / DeepEval license terms | Not re-verified this session | No claims made; revisit only if adoption is proposed |

## 9. Agreement Statement

All three planning documents agree: the same three selected differentiators (Evidence Ledger, Failure Observatory, Adaptive Retrieval Budget), the same rejected/deferred lists, the same milestone order and gates, the same invariants (I1–I13), the same constraints (Z1–Z12), the same risk register (R1–R22), and the same unresolved decisions (D1–D11). The v1 file remains byte-identical to its reviewed SHA256 and is marked historical inside v2's header. No implementation has begun; approval of D1 (and the recommendations in D2, D5–D9, D11) is the gate to Phase 0.
