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
