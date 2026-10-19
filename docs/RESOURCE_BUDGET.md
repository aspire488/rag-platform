# Resource Budget and Decision Record

Owner of record: project owner (via explicit approval, 2026-10-19).
Source specification: `RAG_Platform_Master_Plan_v2.3.md` §10, §14.

## 1. Owner decision record (§14, recorded 2026-10-19)

| ID | Decision | Disposition |
|---|---|---|
| D1 | Implementation start | **Approved 2026-10-19** — Phase 0 only authorized first; Phase 0.5+ require review gates |
| D2 | MiniLM cache copy C:→D:, project-local `HF_HOME` | **Approved 2026-10-19** — executes at Phase 3 |
| D3 | Post-BG-1a model strategy | **Deferred** — default is retrieval-only unless the owner explicitly approves another option |
| D4 | OLLAMA_MODELS relocation | Closed/withdrawn (2026-10-18) |
| D5 | SQLite (WAL + FTS5) for all storage | **Approved 2026-10-19** |
| D6 | M2 auth: PBKDF2 API keys, stdlib-only | **Approved 2026-10-19** |
| D7 | Locally authored synthetic eval corpus | **Approved 2026-10-19** |
| D8 | Local `git init`, no remote/push | **Approved 2026-10-19** |
| D9 | Advisory model tiers off by default, never gating | **Approved 2026-10-19** |
| D10 | Conditional LLM-free graph experiment | **Deferred** to M3 exit review; only if measurements justify |
| D11 | Provisional quality/latency margins | **Approved 2026-10-19** — provisional; re-frozen after M1 baseline |
| D12 | BG-1a `too-slow` floor | **Confirmed 2026-10-19: 4 tok/s** (provisional floor; measured tok/s reported regardless; recorded here per §10.3 before BG-1a) |

## 2. BG-1a measured values

Pending — to be filled only when BG-1a actually runs (Phase 0.5). Nothing is pre-filled (Z8).

## 3. Budgets — provisional estimates until measured (plan §10.1; all rows are estimates)

| Component | RAM (est.) | VRAM (est.) | Disk (D:) | Notes |
|---|---|---|---|---|
| FastAPI + uvicorn | 150–300 MB | 0 | — | 1 worker |
| venv (torch CPU + transformers + qdrant-client + app) | — | — | ~3–4 GB | uv cache on D: |
| MiniLM embeddings (lazy) | 300–700 MB | 0 | 87 MB cache copy | CPU only |
| Qdrant local mode | 50–300 MB | 0 | ~1.5 KB/chunk | in-process |
| SQLite (WAL) | 10–80 MB | 0 | MB-scale | traces bounded by retention cap |
| Ollama idle | 200–400 MB | 0 | — | models stay on C: (existing; read-only) |
| `qwen3:8b` loaded (CPU path) | +5–6.5 GB | 0 | — | mmap caveat; unload via `keep_alive` |
| `qwen3:8b` loaded (partial GPU) | ~0.5–1 GB host | up to ~5.5 GB | — | proven only via `ollama ps` |
| **Steady state total** | **~0.8–1.5 GB** | 0 | — | must hold with browser + VS Code open |
| **CPU-generation worst case** | **~7–9 GB** | 0–5.5 GB | — | ≥3 GB system free-RAM floor enforced |

## 4. Phase 0 environment facts (measured 2026-10-19, this machine)

| Fact | Measured value | Evidence |
|---|---|---|
| Python (project venv) | 3.14.5 at `D:\rag-platform\.venv` | `setup.ps1` report + import smoke test |
| uv | 0.11.32 | preflight |
| torch | 2.14.1+cpu (CPU-only index; no CUDA packages) | import smoke test |
| sentence-transformers / transformers | 6.1.0 / 5.20.1 | `uv.lock` |
| qdrant-client | 1.19.1 — **legacy `.search()` API removed; use `.query_points()`** (§12 sample command uses the removed API — implementation will follow the current API) | self-check run |
| FTS5 (R20) | **Available** — sqlite 3.50.4 with FTS5 + MATCH verified | `scripts\check_fts5.py` |
| Qdrant local persistence + payload filter | **Verified** — persisted across client reopen; filter returned only expected id | Phase 0 self-check |
| pip-audit baseline | **No known vulnerabilities found** (torch `2.14.1+cpu` skipped: not on PyPI audit DB) | `uv run pip-audit` |
| License review (Z1) | 87 distributions; all permissive (MIT/BSD/Apache/PSF/0BSD); 39 with no license metadata had license files verified in dist-info; 0 flagged | `reports\license_baseline_phase0.txt`, `reports\license_files_phase0.txt` |
| D: free at setup | 192.53 GB (≥10 GB working minimum) | `reports\setup_phase0_20261019-102817.md` |
| C: free at setup | 4.72 GB — **informational only; no C: gate exists (Z9)** | same |
| Ollama | reachable; models: `qwen3:8b` (5.2 GB), `qwen3:latest` (same digest as qwen3:8b — alias evidence for A1), `qwen2.5-coder:7b` (4.7 GB); read/executed in place on C: | `ollama list`, `/api/tags` |
| Git | 2.54.0; repo initialized local-only, **zero remotes**, no push | Phase 0 |

R7 contingency (Python 3.12/3.13 fallback): **not needed** — the full dependency set resolved and imported on 3.14.5.

## 5. Resource safety policy (owner requirement, 2026-10-19 — priority over features and benchmarks)

**Standing rule: system resource safety outranks every feature and every benchmark.** Never sacrifice system responsiveness to pass a benchmark or a feature gate; blocked or unavailable results are recorded honestly. The objective is a useful, measurable RAG engine that runs comfortably on this laptop — not the maximum number of features.

### 5.1 RAM thresholds and the start gate (never lowered automatically)

| Control | Value | Enforcement |
|---|---|---|
| Normal stop threshold | available RAM < **3 GB** → abort | 1 Hz sampler (`bench_model.py`) |
| Emergency backstop | available RAM < **2 GB** → immediate abort | same sampler, higher priority |
| Start gate | idle available RAM < **3 GB** → **no model load at all**; wrapper refuses to invoke the driver (exit 2, `blocked`), driver refuses to start tests | wrapper guardrail 5 + driver start gate |
| Swap | any new swap activity above baseline (+4 MB epsilon) → abort, verdict `blocked` | sampler |
| Runtime cap | 15 minutes → abort, verdict `blocked` | sampler |
| Manual abort | sentinel file `data\tmp\BG1A_ABORT` or Ctrl+C → abort, verdict `blocked`, model unloaded | sampler / driver |

The 3 GB / 2 GB thresholds are owner-set and **supersede the plan §10.3 step-6 default (2 GB floor); plan files remain untouched**. They are never lowered automatically — any change is a separate owner decision. Abort classification is by explicit kind recorded at abort time (`ram-floor`/`emergency` → `too-heavy`; `swap`/`runtime-cap`/`manual`/`start-gate` → `blocked`), never by free-text matching. Every exit path (normal, abort, Ctrl+C, internal error) unloads the model (`keep_alive=0`) and records the verified final `/api/ps` state in the evidence file.

### 5.2 Binding limits for all phases (carried from plan §6.2/I7; hard caps, not suggestions)

- **Model calls: sequential only.** Generation concurrency = 1 (plan M4: parallel `/query` → ≤1 generation in flight, excess → fast 503). BG-1a issues one request at a time.
- **Input/chunking:** chunk ~1000 chars (overlap ~150); prompt chars ≤ 8000.
- **Context:** context chunks ≤ 8; `num_ctx` ≤ 4096 (BG-1a: 2048 load / 4096 sustained; Phase 6 generation defaults are set only from measured BG-1a results, never assumed).
- **Retrieval:** top-k default 8, max 20 (adaptive tiers 4/8/14; adaptive off by default, D9).
- **Generation caps:** `num_predict` bounded per request (BG-1a: 32 / 512); temperature 0 for benchmarks.
- **Timeouts/retries:** Ollama timeouts 15 s / 60 s; retries ≤ 2.
- **Caches:** bounded by design (I7): entry size caps, fingerprint keys, revalidation on hit; kill switch bypasses cache entirely. Traces/logs size-bounded with rotation; trace excerpt ≤ 200 chars; ledger claims ≤ 40 per answer.
- **Background activity:** no persistent daemons; Ollama unloaded (`keep_alive=0`) after every benchmark request; the 1 Hz sampler exists only during a BG-1a run.
- **Deferred (needs separate approval):** graph experiment D10 (depth ≤ 2 / ≤ 50 nodes) — deferred to M3 exit.

### 5.3 Retrieval-only stays fully supported (D3 default)

Retrieval, citations, and evidence reporting never depend on the generation model. **Local generation stays disabled by default** until BG-1a records a `usable-cpu`/`usable-gpu` verdict **and** the owner explicitly approves enabling it. If generation is unsafe, too slow, or too resource-intensive, the platform continues retrieval-only — recorded honestly, never faked. The plan requires every stage to degrade to retrieval-only with an explicit logged reason (Z11/I5).

### 5.4 Owner restrictions stricter than the plan

- **No fallback-model benchmark** (`qwen2.5-coder:7b`) without separate owner approval — stricter than plan §10.3 step 8, which would allow a same-session fallback check.
- **Strict endpoint validation** in the benchmark wrapper and driver: scheme `http`, host exactly `localhost` or `127.0.0.1`, port exactly `11434`; prefix matching is never accepted.
- **D:-only storage, no C: free-space gate** (plan Z9, already enforced by `setup.ps1`); no Docker, no new models, no global configuration changes, no paid/cloud services without separate approval; dependency set stays minimal (torch CPU-only, no CUDA wheels; no Redis/Neo4j/postgres/rerankers/dashboards — plan Z7).

### 5.5 Incident record (2026-10-19, disclosed to the owner same day)

Two unauthorized benchmark attempts executed during script verification due to test-harness command errors (PS 5.1 nested-shell quoting): `reports\bg1a_20261019-104558.*` (model loaded ~87 s, emergency abort at 1.85 GB, unloaded, recovered; **no throughput data measured**) and `reports\bg1a_20261019-105645.*` (start gate + RAM-floor abort in 0.42 s, **model never loaded**). **Neither is a valid BG-1a result** — §2 remains pending. Safety mechanisms (abort, unload, evidence, honest verdicts) worked; the fixes above (strict endpoint validation, kind-based classification, always-reported cleanup, wrapper start gate) were applied afterwards. An offline selftest (`.\scripts\bench_model.ps1 -SelfTest`) verifies stop conditions, guards, and evidence paths without contacting Ollama or loading any model.

### 5.6 90 % total-system-RAM ceiling (owner requirement; mirrored from plan v2.4 §2 Z14 / §10.4)

**Policy (binding):** the project must not intentionally continue operating above **90 % total system RAM usage**. Admission: before starting any potentially resource-intensive project operation, observe total system RAM usage and do not start it if the ceiling is already reached. Monitoring: while a project operation runs, total usage is watched. Abort: if total usage reaches the ceiling during a project operation, abort **the project's own operation** (recorded honestly, per §9.1 status semantics of plan v2.4 — a safety abort is `blocked`, never a pass).

**What this policy is (honest scope):**
- It is a limit on the **project's intended operation**, not a claim about the machine as a whole. Windows, drivers, and unrelated processes can raise total system RAM usage independently of anything this project does, and a user-space application cannot control or guarantee against that. The policy is therefore a conservative **admission → monitoring → abort** requirement applied to our own work only; it never authorizes acting on other processes or the operating system.
- **The 90 % figure is a ceiling, not a budget.** The stricter §5.1 controls (3 GB available-RAM start gate before any model load or benchmark, 2 GB emergency floor abort, swap-activity abort, 15-minute runtime cap, immediate manual abort via sentinel file or Ctrl+C) remain fully in force and are never relaxed by this subsection. No new threshold other than the owner-stated 90 % total-usage ceiling is introduced; §5.1–§5.5 are unchanged.
- **Implementation status (honest, as of 2026-10-19):** no 90 % monitor or enforcement code exists in the repository. The only enforced controls today are the §5.1 available-RAM/swap/runtime thresholds inside the BG-1a benchmark scripts, which activate only during an authorized benchmark run (and BG-1a remains blocked pending separate approval). This subsection records the binding policy and the resulting future implementation obligation — **it is not verified enforcement.** Any later enforcement must be evidenced by tests before compliance is claimed.

This subsection closes the open item recorded in plan v2.4 §10.4 (mirroring the ceiling into this file at the next owner-authorized documentation update). Authorization to add this subsection: final implementation-kickoff instruction §2 (update only §5 of this file; v2.4 plan files untouched).
