# RAG Platform — Strategy Review (Build vs. Reuse vs. Differentiate)

**Project root:** `D:\rag-platform`
**Document:** `RAG_Platform_Strategy_Review.md` — version 1.0, 2026-10-18
**Purpose:** Critical review of the original master plan (`RAG_Platform_Master_Plan.md`, left untouched) against existing open-source solutions, evaluation of innovation candidates, and a recommended project direction.
**Companion documents:** `RAG_Platform_Master_Plan_v2.md` (the revised plan), `RAG_Platform_Plan_Diff.md` (what changed and why).
**Status:** Planning only. No code, Git, packages, models, services, or system changes were made. External research was performed; no benchmarks were run.

---

## 1. Method and Evidence Base

| What was inspected | How |
|---|---|
| Existing plan | Full read of `D:\rag-platform\RAG_Platform_Master_Plan.md` (49,061 bytes; SHA256 `7E739904…C9FDD` recorded before this review; file not modified) |
| Workspace | Directory listing: contains only the original plan file |
| AnythingLLM | Official docs (`docs.anythingllm.com`), GitHub repo page, marketplace listing |
| RAGFlow | Official docs (`ragflow.io`), GitHub README + release notes (v0.27.x), FAQ |
| Dify | Official docs (`docs.dify.ai`), GitHub `LICENSE` (full text, two copies) |
| Open WebUI | Official docs (`docs.openwebui.com`), GitHub README, **two GitHub security advisories (2026)** |
| Onyx (Danswer) | GitHub `LICENSE` + `backend/ee/LICENSE`, official docs (`docs.onyx.app`), `onyx.app/llms.txt` |
| Ragas / TruLens / DeepEval | Official docs and PyPI/GitHub pages |
| GraphRAG systems | LightRAG, HippoRAG/2, MS-GraphRAG, Fast-GraphRAG, LinearRAG — via peer-reviewed papers (ICLR 2026, EMNLP 2025, arXiv surveys) with measured token/time costs |
| Citation evaluation | ALCE (EMNLP 2023), ALiiCE (NAACL 2025), FActScore (EMNLP 2023), CAGE (2026) |

**Explicit limitations of this evidence base:**
- Claims about competitor behavior come from **documentation and advisories**, not from running the competitors on this machine. Where docs were silent, status is `unknown`, not "missing".
- Peer-reviewed cost numbers (e.g., GraphRAG token usage) were measured on **other hardware and models**; they are directional evidence, not predictions for this laptop.
- Absence of a feature in documentation is **not proof** the feature does not exist in code. Every "gap" below is phrased as "not found in reviewed docs".
- This review did not run any benchmark, launch any model, or install anything (per task constraints).

---

## 2. What Existing Projects Already Provide

### 2.1 Platform comparison

| Project | License (verified) | Deployment | Document permissions (from docs) | Citations | Eval/diagnostic facilities | Fit for this laptop |
|---|---|---|---|---|---|---|
| **AnythingLLM** | **MIT** | Desktop app (single-user) or **Docker** (multi-user) | Multi-user RBAC (admin/manager/default) **Docker version only**; per-workspace document scope; embedded SQLite + LanceDB, no external DB | Workspace sources listing (chunk-level) | None found beyond chat logs | Desktop: single-user only. Multi-user ⇒ Docker required |
| **RAGFlow** | **Apache-2.0** | **Docker required** (≥24), recommended 4 cores/16 GB RAM/**50 GB disk** | Release notes state: **"The Team/Me permission model is not yet supported"** (open-source) | Strong: chunk visualization, "traceable citations", key-reference quick view | Retrieval-testing UI; no frozen-dataset offline harness found | Heavy: multi-service stack (DB/object storage/search engine), 50 GB disk, Docker daemon |
| **Dify** | **Modified Apache-2.0**: multi-tenant operation **prohibited without commercial license**; frontend logo/copyright restrictions | Docker Compose (PostgreSQL + middleware; Weaviate etc.) | Workspace = tenant per license text; fine-grained document ACL not the focus | RAG pipeline with sources | LLMOps logs/monitoring; cloud-tied | Docker + several GB; workflow platform, not a RAG engine core |
| **Open WebUI** | Not re-verified this session (`unknown`) | Docker or native desktop app | RBAC with knowledge-base sharing; **permission logic is additive** ("True takes precedence over False" across groups); **two 2026 security advisories on RAG authorization** (below) | Sources list with scores; `RAG_SOURCE_METADATA_KEYS` for page metadata | Built-in model arena/A/B; no per-stage failure attribution found | Native app exists; RAG is one feature among many |
| **Onyx (Danswer)** | Core **MIT**; `ee/` directories **Enterprise License** (subscription terms for production) | Docker compose; **PostgreSQL + OpenSearch + Redis** | **"Different access to documents is only available in the Enterprise Edition"** (official docs) — permission syncing also Enterprise | Citations APIs exist | Enterprise analytics (usage graphs) | Heavy infra; the headline permission feature is subscription-gated |

**Open WebUI advisories (directly relevant to permission-aware RAG):**
- `GHSA-7r82-qhg4-6wvj` / **CVE-2026-44554** (High, CVSS 8.1): "Knowledge Base Destruction and RAG Poisoning via Unauthorized Collection Overwrite" — *"no collection authorization check"* in `retrieval.py`; attacker with any account can delete/poison another user's knowledge base.
- **CVE-2026-45397** (Medium): unauthenticated disclosure of live RAG pipeline configuration (`GET /api/v1/retrieval/`).
These are evidence that per-collection authorization in multi-user RAG is **easy to get wrong in real shipped code** — not a hypothetical threat model.

### 2.2 Evaluation frameworks

| Framework | What it provides (verified) | Limitation for this project |
|---|---|---|
| **Ragas** | Faithfulness, context precision/recall, response relevancy; Ollama via OpenAI-compatible endpoint supported | Headline metrics are **LLM-judge driven**; framework-embedded; not a runtime diagnostic |
| **TruLens** | OpenTelemetry tracing, "RAG triad" (context relevance/groundedness/answer relevance), LLM judges, Ollama quickstart | Judge-centric; heavier instrumentation model; scores, not deterministic per-stage attribution |
| **DeepEval** | Pytest-style LLM eval tests; cloud platform on top | Same judge-dependence; cloud extras are out of scope under zero-cost policy |

**Conclusion:** rigorous *offline evaluation* exists as libraries, but as **LLM-judge-centric tooling** — none ships deterministic, per-stage, offline failure attribution inside a lightweight local engine. (Absence not proven beyond docs reviewed.)

### 2.3 GraphRAG systems (measured evidence)

| System | Indexing cost (paper-measured, other hardware) | Query-token cost | Notes |
|---|---|---|---|
| MS-GraphRAG | 292 s / **654,673 tokens** ("novel" corpus) | ~38,707 (local) to ~331,375 (global) avg tokens | Global mode dominated by token cost |
| LightRAG | 710 s / 474,172 tokens | ~100,832 avg tokens | Deprecation note in RAGFlow v0.27 (they removed their GraphRAG/RAPTOR UI) |
| HippoRAG / 2 | 77 s / 177,961 tokens | ~7,208 / ~1,008 | Personalized PageRank is math-only at query time |
| Fast-GraphRAG | 282 s / 251,817 tokens | ~4,204 | Dijkstra/PCST-style traversal |
| **LinearRAG** (ICLR 2026) | **0 generation-LLM calls** (NER + embeddings only) | low | The lightweight pattern: graph built without generative LLM |

Additional verified findings:
- **ICLR 2026 (LinearRAG paper):** GraphRAG methods achieved *lower context relevance* (36.86–54.61%) than vanilla RAG (62.87%) on their benchmark; LightRAG used **4,933 prompt tokens/query vs ~top-5 vanilla**.
- **"RAG vs GraphRAG: A Systematic Evaluation":** *"RAG and GraphRAG exhibit complementary behaviors rather than a consistent winner"* — RAG wins single-hop/detail queries; GraphRAG wins multi-hop.
- **"BM25 Wins at Scale" (arXiv 2607.26497):** scaling curves cross around **10M tokens**; below that, simple retrieval is competitive or better.

**Implication for this project:** graph construction via generative LLM (LightRAG/GraphRAG style) costs **hundreds of thousands of local-8B tokens** — hours of generation on this laptop — and query-time token inflation fights the 16 GB budget. The defensible graph direction is the **LinearRAG/HippoRAG pattern: LLM-free graph construction** (NER/heuristics + embeddings + PageRank math), treated as a **measured experiment with kill criteria**, not a commitment.

### 2.4 Citation-evaluation literature (grounding for Candidate A)

| Method | What it establishes (verified) |
|---|---|
| **ALCE** (EMNLP 2023) | Citation **recall** (is the answer fully supported by cited passages?) and citation **precision** (are any cited passages irrelevant?) via an NLI model; automatic metrics agreed with human annotation **85.1% / 77.6%** — i.e., even the research-grade automatic method is imperfect |
| **ALiiCE** (NAACL 2025) | Dependency-tree parsing into **atomic claims** for finer-grained citation scoring; sentence-level metrics hide multi-claim errors |
| **FActScore** (EMNLP 2023) | Decompose generation into **atomic facts**, score % supported by a knowledge source; automated variant <2% error vs human (with a strong LM) |
| **CAGE** (2026) | "Evidence-boundary overrun" metrics: fraction of unsupported atomic facts — the framing we adopt for abstention |

**Implication:** claim-level support measurement is established *research practice*, typically executed with **NLI models or strong LLM judges**. On this machine: an NLI model would be a **new download (approval-gated)**; a local 8B judge is possible but **slow and non-deterministic**. The honest design splits support checking into a **deterministic tier** (containment/lexical) and an **advisory tier** (local model), and reports them separately.

---

## 3. Gap Analysis — What No Reviewed Project Ships as a Local, Zero-Cost, Inspectable Whole

| Capability | AnythingLLM | RAGFlow | Open WebUI | Onyx | Ragas/TruLens | **Our opportunity** |
|---|---|---|---|---|---|---|
| Runs fully local, no Docker, ≤ a few GB disk | Partial (desktop, single-user) | No (Docker, 50 GB) | Partial | No (PG+OpenSearch+Redis) | Yes (as libs) | **Yes — engine with embedded stores** |
| Zero-cost multi-user document ACL **without enterprise license** | Docker-only | Not supported (open source, stated in release notes) | Exists but additive logic + 2026 authorization CVEs | **Enterprise-only** | n/a | **Dual-phase enforcement + adversarial matrix as engineering standard** |
| Claim-level evidence ledger (supported / inferred / conflicting / unsupported) with abstention | Not found | Chunk-level citations only | Sources list | Not found | Eval-only (offline) | **Core innovation A** |
| Deterministic offline per-stage failure attribution (retrieval miss vs ranking vs generation vs citation vs model-down) | Not found | Not found | Not found | Enterprise analytics (usage, not failure attribution) | Judge-based scoring | **Core innovation B** |
| Frozen local eval harness with dataset hashes and honest pass/fail/skipped/blocked statuses | Not found | Retrieval-testing UI only | Arena/A/B only | Enterprise analytics | The libraries, but judge-centric; no frozen-corpus workflow | **First-class eval core (v2 M1)** |
| Budgeted adaptive retrieval (deterministic, no LLM in the policy) | Configurable k only (assumed; not verified) | Configurable (assumed) | Configurable (assumed) | Not found | n/a | **Core innovation C (bounded)** |
| LLM-free multi-hop graph experiment with measured kill criteria | n/a | Deprecated their GraphRAG UI | n/a | LLM-based KGs (EE) | n/a | **Conditional M4 experiment** |

Every "Not found" above means: **not found in the documentation reviewed** — it is a gap in *shipped, documented, locally-runnable, zero-cost form*, which is the standard that matters here.

---

## 4. Innovation Candidate Evaluation

Scoring: 1 (poor) – 5 (excellent). "Security risk" is inverted (5 = lowest risk). Candidates A–E are the hypotheses from the reassessment task; F–J are additional candidates introduced by this review.

| ID | Candidate | User value | Differentiation | Feasible on laptop | Zero-cost | Low complexity | Security (5=safe) | Evaluable | **Total /40** | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| **A** | **Evidence Ledger** (claim→span provenance, support classes, abstention) | 5 | 4 | 4 | 5 | 3 | 4 | 5 | **34** | **SELECT (core 1)** |
| **B** | **RAG Failure Observatory** (per-stage deterministic attribution, JSON traces, offline probes) | 4 | 4 | 5 | 5 | 4 | 4 | 5 | **35** | **SELECT (core 2)** |
| **C** | **Adaptive Retrieval Budget** (bounded, deterministic, hard caps) | 4 | 3 | 4 | 5 | 3 | 4 | 4 | **27** | **SELECT (core 3, bounded + kill criteria)** |
| **D** | Contradiction & Freshness Detection | 3 | 3 | 3 (semantic part: 2) | 5 | 2 | 4 | 3 | 23 | **PARTIAL — deterministic skeleton folded into A** (document timestamps in provenance; conflict flag on seeded value disagreements). Full temporal/semantic contradiction engine: **rejected** (needs NLI/LLM per pair; complexity > value here) |
| **E** | Permission-Safe Memory (conversational/derived knowledge) | 3 | 2 | 3 | 5 | 1 | 2 | 3 | 19 | **REJECT** — revocation-correct derived memory is a research-scale security problem; semantic cache (M4) + document retrieval cover most value at a fraction of the risk |
| **F** | Span-level provenance anchors (char offsets/page/heading per citation) | 5 | 3 | 5 | 5 | 4 | 5 | 5 | **32** | **FOLD INTO A** — deterministic, cheap, makes citations *human-verifiable*, not just system-verifiable |
| **G** | Offline counterfactual probe (remove top chunk → answer unchanged ⇒ parametric-memory suspicion) | 4 | 4 | 3 (eval-time only) | 5 | 3 | 4 | 4 | 27 | **FOLD INTO B** — costs generation time only in offline eval; strongest failure-attribution signal available without another model |
| **H** | Ingestion-time injection quarantine (deterministic scan ⇒ `untrusted` payload flag + report) | 4 | 3 | 5 | 5 | 4 | 5 | 4 | 34 | **FOLD INTO security layer** (M1/M2) — Open WebUI's 2026 RAG-poisoning CVE shows the threat is real; patterns scan is cheap and deterministic. Not counted as a standalone core (it is diligence, not differentiation) |
| **I** | SQLite FTS5 BM25 + dense fusion (RRF) | 4 | 1 (well-established; Open WebUI ships hybrid) | 5 | 5 | 4 | 5 | 4 | 29 | **ADOPT AS INFRASTRUCTURE** (M1) — honest classification: existing, established functionality; FTS5 is in stdlib SQLite, zero new services; supported by the BM25-at-scale evidence |
| **J** | Evidence-keyed semantic cache (authz fingerprint + corpus version in key; revalidate on hit) | 4 | 2 | 5 | 5 | 3 | 3 | 4 | 26 | **KEEP AS M4 INFRASTRUCTURE** (evolution of v1 M3) — not a differentiator, but required for resource-aware operation |

**Selection: exactly three cores — A, B, C — with F and G folded into A and B respectively.** This honors "prefer two well-executed differentiators over five superficial features" while staying within the three-core limit. Rejected outright: E (permission-safe memory) and the full version of D (semantic contradiction engine).

### 4.1 Novelty honesty (required classifications)

| Claim someone might make | Correct classification |
|---|---|
| "A RAG system with citations" | **Existing, well-established** (RAGFlow, Open WebUI, everything) |
| "Citation quality measurement" | **Existing research practice** (ALCE/ALiiCE/FActScore, 2023–2026) |
| "Trace/observability for LLM apps" | **Existing** (TruLens OTEL, LangSmith-class products) |
| "Adaptive retrieval depth" | **Existing research direction** (Self-RAG/CRAG/Adaptive-RAG loops) |
| **"Runtime claim-level support classification with span anchors and abstention, deterministic-first, zero-cost, no NLI download, evaluated on a frozen local corpus"** | **Useful integration + engineering improvement.** No novelty claim beyond this. Not established as a shipped combination in reviewed docs; absence not globally proven |
| **"Deterministic per-stage failure attribution with offline counterfactual probes inside a lightweight local engine"** | **Useful engineering improvement** over judge-only tooling |
| **"Budgeted deterministic adaptation under hard RAM/latency caps for a local 8B model"** | **Useful engineering improvement**; must beat fixed-k or be removed |

No genuinely novel research claim is made or required by this project.

---

## 5. Build vs. Reuse Recommendation

### Option A — Use and customize an existing platform
- **Rejected.** Every candidate forces a bad trade: AnythingLLM's multi-user requires **Docker** (and this machine's Docker data sits on a nearly-full C:); RAGFlow requires Docker + 50 GB and states its **permission model is not yet supported** in open source; Dify is a workflow platform with a **license restricting multi-tenant use**; Onyx puts **document-level permissions behind an Enterprise subscription** (violating zero-cost for the exact feature we care about); Open WebUI's additive permission logic plus two 2026 RAG-authorization CVEs argue against treating its retrieval authorization as a safe foundation.
- Customizing any of them to host a claim-level ledger + deterministic failure attribution means deep surgery in large foreign codebases (Node.js/Go/Python mixes) — higher maintenance cost than the differentiators are worth, and the eval core still doesn't exist there.

### Option B — Build the original v1 system from scratch
- **Rejected as stated.** The v1 plan was a (small) generic RAG platform: ingestion, retrieval, generation, tenancy, cache, routing, GraphRAG. Four of those seven are solved problems where our version would be a thinner reimplementation, and M4 (GraphRAG) as specced fights the hardware (§2.3). Building everything means the scarce resource — this laptop's time and RAM — goes to table stakes instead of differentiators.

### Option C — Use existing components; build a small number of measurable differentiators ✅ **RECOMMENDED**
- **Adopt as the direction.** Keep the v1 plan's sound substrate (Python/uv, FastAPI+CLI, Ollama, cached MiniLM, Qdrant local mode, SQLite, NetworkX-only-if-measured, modular monolith, egress-off, frozen local eval) and add **FTS5 hybrid retrieval** (existing tech, zero new services).
- **Spend the effort budget on three things nothing reviewed ships locally**: the Evidence Ledger (A), the Failure Observatory (B), and a bounded Adaptive Retrieval Budget (C) — each with a baseline, measurable gate, and kill criteria.
- **Demote GraphRAG** from a milestone commitment to a **conditional, LLM-free, measured experiment** (M4), per the paper evidence in §2.3.
- **Reorder milestones** so the evaluation foundation lands in M1 and security hardening lands *before* the trust features (so the ledger/observatory are born inside the authorization boundary, not retrofitted).

**One-sentence direction:** *a local-first RAG **engine with an evaluation-and-diagnostic core** — not another all-in-one chat platform — whose value is verifiable evidence, inspectable failures, and honest measurement on hardware we actually have.*

---

## 6. Assumptions and Evidence Limitations (explicit)

| # | Assumption / limitation | Status |
|---|---|---|
| S1 | Competitor feature claims rest on documentation, not local runs | **Unverified by execution** |
| S2 | Open WebUI license terms | **Not re-verified this session (`unknown`)** — no license claims made about it |
| S3 | Ragas/DeepEval license terms | **Not verified** — no license claims made; not adopted |
| S4 | Whether competitors do *post-retrieval* re-authorization | **Unknown** (not found in reviewed docs; not proven absent) |
| S5 | Paper-measured GraphRAG token costs transfer directionally to this hardware | **Directional only**; our own costs measured at M4 if the experiment proceeds |
| S6 | Deterministic containment heuristics can classify "direct support" reliably | **Hypothesis** — measured on the frozen corpus at M3; advisory tier exists precisely because this may be weak |
| S7 | Local 8B model can decompose claims at usable speed/quality | **Hypothesis** — benchmarked in M3 with explicit simplification fallbacks (ledger degrades to anchors-only) |
| S8 | FTS5 is compiled into CPython 3.14's bundled SQLite on this machine | **To verify at Phase 0**; fallback is dense-only retrieval (no dependency added) |
| S9 | `qwen3:8b` is usable for generation at all on this laptop | **Unmeasured** — BG-1 benchmark protocol exists precisely to decide this; no downloads until measured + approved |

---

## 7. Sources Consulted (all accessed 2026-10-18)

1. AnythingLLM docs — security/access, installation overview, configuration: `docs.anythingllm.com/features/security-and-access`, `docs.anythingllm.com/installation-docker/overview`, `docs.anythingllm.com/configuration`
2. RAGFlow — GitHub `infiniflow/ragflow` README + release notes (v0.27.x, Sep 2026), `ragflow.io/docs/` quickstart + FAQ + launch-from-source
3. Dify — GitHub `langgenius/dify` `LICENSE` (full text), `docs.dify.ai` self-host prerequisites
4. Open WebUI — GitHub advisories `GHSA-7r82-qhg4-6wvj` (CVE-2026-44554) and `GHSA-65pg-qhhw-mxwg` (CVE-2026-45397); `docs.openwebui.com` RBAC + knowledge-base docs; GitHub README
5. Onyx — GitHub `onyx-dot-app/onyx` `LICENSE` + `backend/ee/LICENSE`; `docs.onyx.app` access-controls page ("Enterprise Edition" for document access); `onyx.app/llms.txt` (stack: PostgreSQL/OpenSearch/Redis)
6. Ragas docs (`docs.ragas.io`), TruLens GitHub + `trulens.org`, DeepEval `deepeval.com`
7. LightRAG — EMNLP 2025 Findings paper; GitHub `HKUDS/LightRAG`
8. LinearRAG — ICLR 2026 proceedings PDF (efficiency table: token costs of G-Retriever/RAPTOR/E²GraphRAG/LightRAG/HippoRAG/2/GFM-RAG)
9. "RAG vs. GraphRAG: A Systematic Evaluation and Key Insights" — arXiv 2502.11371
10. "When to use Graphs in RAG" — arXiv 2506.05690 (avg-token table: V-RAG ~879–954 vs LightRAG ~100,832; indexing table)
11. "BM25 Wins at Scale" — arXiv 2607.26497 (curves cross ~10M tokens)
12. ALCE — "Enabling LLMs to Generate Text with Citations" (EMNLP 2023; 85.1%/77.6% human agreement)
13. ALiiCE — NAACL 2025 (dependency-tree atomic claims)
14. FActScore — arXiv 2305.14251 (<2% automated-vs-human error with strong LM)
15. CAGE — arXiv 2607.24236 (evidence-boundary overrun metrics)

**Recommended direction, in plain English:** don't clone a chat platform. Build the small, local, inspectable RAG engine the v1 plan already sketched — then make it *defensible* by adding what no reviewed project ships in zero-cost local form: an evidence ledger that says exactly which claims the documents support (and abstains when they don't), a failure observatory that says exactly *where* a bad answer broke, and a hard-budgeted retrieval policy — all proven by a frozen, reproducible, offline evaluation harness on this laptop's real constraints.
