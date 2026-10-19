# RAG Platform — Preflight Audit (v1 + v2 + Strategy Review + Plan Diff)

**Date:** 2026-10-18
**Mode:** Strict — planning and diagnostics only. No application code written, no packages installed, no models downloaded, no `git init`, no global-config or system-setting changes, no plan overwritten, Phase 0 not started.
**Scope:** (A) OpenCode MCP diagnosis + minimal reconnect, (B) cross-document plan audit against the eight required verification points.
**Primary target:** `RAG_Platform_Master_Plan_v2.md` (with `RAG_Platform_Master_Plan.md`, `RAG_Platform_Strategy_Review.md`, `RAG_Platform_Plan_Diff.md`).

---

## Part A — MCP server diagnosis

All 11 servers were reported as errors in-session. The user instruction "fix mcps reconnect all" was treated as explicit approval for the smallest safe MCP corrective action. Backups existed; no secret values were exposed (tokens referenced via `{env:…}`).

### Diagnosis table

| Server | Configured command / transport | Observed error (before) | Essential for *this* task? | Smallest safe corrective action | Status now |
|---|---|---|---|---|---|
| filesystem | `cmd /c npx -y @modelcontextprotocol/server-filesystem D:\rag-platform` (local stdio) | `Request timed out` at connect | Yes — file read/write for this task | Migrate flat→V2 `mcp.servers` shape + `mcp.timeout.startup=60000`; restart service | ✓ connected |
| git | `uvx --with mcp<2.0.0 mcp-server-git --repository <Kio path>` (local stdio) | `Request timed out` | No — repo is a Kio path, not `D:\rag-platform` | same global fix; **recommend retarget to `D:\rag-platform` once git-init approved (D8)** | ✓ connected |
| memory | `cmd /c npx -y @modelcontextprotocol/server-memory` | `Request timed out` | No | same global fix | ✓ connected |
| sequential-thinking | `cmd /c npx -y …` | `Request timed out` | No | same global fix | ✓ connected |
| context7 | `cmd /c npx -y @upstash/context7-mcp` | `Request timed out` | No | same global fix | ✓ connected |
| playwright | `cmd /c npx -y @playwright/mcp@latest` | `Request timed out` | No | same global fix | ✓ connected |
| serena | `C:\Users\joelj\.local\bin\serena.exe start-mcp-server` | `Request timed out` | No | same global fix | ✓ connected |
| chrome-devtools | `cmd /c npx -y chrome-devtools-mcp@latest …` | `Request timed out` | No | same global fix | ✓ connected |
| dbhub | `node …\index.js --transport stdio` | `Request timed out` + earlier `unknown message ID` (server↔client protocol mismatch) | No — points at a Kio DSN on C: | same global fix | ✓ connected |
| firecrawl | `cmd /c npx -y firecrawl-mcp` (+ `FIRECRAWL_API_KEY`) | `Request timed out` | No | same global fix | ✓ connected |
| github | remote HTTP `https://api.githubcopilot.com/mcp/`, Bearer `{env:GITHUB_PAT}` | `getaddrinfo ENOTFOUND api.githubcopilot.com` | No | transient DNS; service restart re-resolved | ✓ connected |

### Root cause (evidence-based, shared infrastructure — not 11 separate faults)

1. **Config-shape / timeout-semantics mismatch (primary, shared).** The config used the **flat V1 layout** (`mcp.<name>`) with **per-server integer `timeout: 60000`**. OpenCode v2.0.32 (installed 11:01, service `serve --service` started 11:06) expects the **V2 layout** `mcp.servers.<name>` with `mcp.timeout` as an **object** `{startup, catalog, execution}`. Under the flat layout the integer timeouts were **not applied to the connect/startup phase**, so all local servers hit the short default connect window and reported `Request timed out`. Measured manual boot times for two representative servers: **memory 4.28 s, git 4.02 s** — healthy, confirming the servers themselves were fine.
2. **github DNS (secondary, transient).** `ENOTFOUND api.githubcopilot.com` occurred in earlier connect waves; at audit time DNS resolves (`140.82.114.22`) and TCP :443 is open. Resolved by the same service restart. Not a config fault.
3. **dbhub protocol mismatch (contributing).** An `unknown message ID` warning (server responding with its own `id:0` initialize) appeared in one wave; cleared after the restart. Not reproducible afterward.

### Confirmed *not* the cause
Not command/path resolution (all binaries present; `npx`→`npx.cmd`, `uvx`, `serena.exe`, dbhub `index.js` all exist). Not missing credentials (`GITHUB_PAT` present, `FIRECRAWL_API_KEY` present). Not network (npm/PyPI/GitHub all reachable). Not 11 individual server crashes (two booted cleanly by hand).

### Corrective action applied (minimal, approved)
- Config migrated to V2 shape: servers nested under `mcp.servers`, `enabled` flags preserved, per-server integer timeouts replaced by `mcp.timeout = { startup: 60000, catalog: 30000 }`.
- `github` given `oauth: false` (header/API-key auth only — correct for a PAT).
- Background service restarted once. No package reinstall, no per-server surgery, no change to any server command other than the documented shape migration.
- **Verified:** `opencode mcp list` → **all 11 `✓ connected`**, and every server's tool set is live in this session's catalog. This is the smallest safe fix; nothing further is required for MCP.

*Note:* `git` still targets a Kio repository path and `dbhub` still targets a Kio DSN. Both are correct-for-their-owners but irrelevant/misleading for `D:\rag-platform`. Retargeting `git` should ride along with the D8 `git init` decision (see Issues). This is a follow-up, not part of the reconnect fix.

---

## Part B — Plan audit

### B0. Verification checklist (the eight required points)

| # | Verification point | Verdict | Evidence |
|---|---|---|---|
| 1 | BG-1 timing vs Phases 0–8 | **Contradiction (High)** | §10.3 title says "run once at M1 start"; §15 Phase 8 places BG-1 at M1 *end* (feeds D3). See Issue H1. |
| 2 | ≥5 GB free on C: setup guard vs current disk | **Unstable assumption (High)** | Guard at §12/§15 Ph.0 (`≥5 GB free on C:`, fail loud). Measured C: free swung **12.4 GB → 4.7 GB** within the audit window. See Issue H2. |
| 3 | All envs/caches/model copies/DBs/logs/reports on D: | **Mostly satisfied (Medium gaps)** | `env.ps1` sets `UV_CACHE_DIR`/`HF_HOME`/`TMP`→D:; `data\` (qdrant, sqlite, cache, corpus, graph, traces) and `reports\` on D:. Gaps: app **log** sink unspecified; **uv package cache** only implied. See Issue M3. |
| 4 | Deterministic evidence matching never confused with semantic entailment | **Satisfied** | §4.1 `directly_supported` = "textually present, *not* semantically true"; `inferred` = "overlap heuristic, *not* logical entailment"; I13 "never emits calibrated probabilities". |
| 5 | Nine-stage observability pipeline + security ordering unambiguous; no unauthorized-content leak | **Ordering contradiction (High)** | §4.2 lists `authn → authz_filter → cache_lookup → …`; §5 lists `authenticate → cache lookup → authorization pre-filter → …`. Cache-before-authz vs authz-before-cache. Leak is *contained* (I1/I2 + independent post-verify) but ordering is ambiguous. See Issue H3. |
| 6 | Practical verification steps for FTS5 / Qdrant persistence+filtering / egress / Python compat | **Partially satisfied (Medium)** | Measured: **FTS5 available** (sqlite 3.50.4, `fts5` MATCH works). But plan gives no *concrete commands* for any of the four. See Issue M4. |
| 7 | Synthetic eval set self-bias risk | **Acknowledged, mitigation thin (Medium)** | R19 (High-likelihood author bias) + §9.3 "gold authored by same author". Mitigation = frozen dev/test split only; no blind holdout / second author / difficulty stratification. See Issue M5. |
| 8 | Gates achievable without assuming GPU offload or unmeasured free RAM | **Mostly satisfied (Medium)** | §10.3 hard rule: "GPU offload … proven via `ollama ps`, never assumed"; stop-conditions use *measured* free-RAM + swap. Residual: M1 gate 9 "steady-state ≤1.5 GB" carries a fixed RAM floor not yet reconciled with live 1.4 GB free. See Issue M6. |

### B0b. Cross-cutting measurements (read-only, this machine, 2026-10-18)

- **RAM:** 1.4 GB free / 15.2 GB total. **C:** 4.7 GB free. **D:** 194.5 GB free. (C: free is volatile — see H2.)
- **FTS5:** available; `MATCH` returns rows. sqlite 3.50.4.
- **Python packages:** `sentence-transformers`, `torch`, `fastapi`, `pypdf`, `psutil`, `pytest`, `httpx`, `pydantic`, `networkx`, `pytest_cov` installed. **`qdrant-client` MISSING** (planned Phase 0 install → D:; correct — not needed at preflight). **`pip_audit` MISSING** (planned Phase 0).
- **v1 plan integrity:** SHA256 `7E739904BBB46B3A57CAB94012DF767BEA3C495F53B02560F9AD057052EC9FDD` — **byte-identical, untouched.** ✓
- **Ollama:** v0.40.4; `qwen3:8b`, `qwen3:latest`, `qwen2.5-coder:7b` present. RTX 3050 6 GB, torch CPU-only `2.13.0+cpu`, no CUDA toolkit. (No GPU-offload assumption anywhere — consistent with R2/§10.3.)

---

### Prioritized issues

Severity: **Critical** blocks a Phase gate or leaks unauthorized content. **High** risks wasted work / rework / security-misunderstanding. **Medium** weakens a gate or verification. **Low** polish.

#### Critical
None. No issue found that would leak unauthorized content (I1/I2/I9 + independent post-verify + authz-filter hold) or irrecoverably block a Phase 0–8 gate.

#### High

**H1 — BG-1 timing contradiction (Item 1).**
*Sections:* `RAG_Platform_Master_Plan_v2.md` §10.3 title (line ~431) vs §15 Phase 8 (line ~530) and §7.1 gate 7 (line ~297).
*Problem:* §10.3 says BG-1 runs "once at M1 start; no benchmarks during planning," but Phase 8 lists BG-1 as the *last* M1 phase and gate 7 requires the BG-1 report. Running the model benchmark at M1 *end* means every earlier phase is built against an unvalidated model assumption; running it at M1 *start* conflicts with the "no benchmarks during planning" framing (BG-1 is a planning activity).
*Proposed wording (§10.3 title):* "Safe benchmark protocol BG-1 (runs **after Phase 0 and before Phase 1** — model verdict gates M1 acceptance; no benchmarks during the planning/pre-approval phase)." Move the §15 Phase 8 BG-1 entry to a pre-M1 gate ("Phase 0.5 / M1-entry") feeding D3, and reword gate 7 to reference that entry gate.

**H2 — ≥5 GB C: guard is an unstable assumption (Item 2).**
*Sections:* §12 (`setup.ps1` asserts "C: free space ≥ 5 GB (fail loud)"); §15 Phase 0; R6 (C: overflow).
*Problem:* The guard is *correct as written* and must **not** be changed (per constraint). But it asserts against a **moving** value. Measured C: free moved 12.4 GB → 4.7 GB during this audit (OneDrive sync / temp / pagefile). A hard `setup.ps1` assert will pass-or-fail depending on the moment, producing nondeterministic setup. This is a real, measured flakiness risk, not a defect in the guard threshold.
*Proposed wording (add to §12, do NOT alter the threshold):* "`setup.ps1` asserts C: free ≥ 5 GB (fail loud) **and prints a C:/D: free-space snapshot into the setup report. If the assert fails, the report records the observed value and the run is marked `blocked` (not `failed`) so a transient OneDrive/temp spike can be re-run rather than treated as a hard fault. No fallback that writes to C: is permitted.**"

**H3 — Pipeline stage-ordering contradiction between §4.2 and §5 (Item 5).**
*Sections:* §4.2 (line ~103) `authn → authz_filter → cache_lookup → retrieval → post_verify → context_build → generation → ledger → response`; §5 (line ~232) `authenticate → derive tenant → cache lookup (fingerprint-checked) → authorization pre-filter → …`.
*Problem:* The two nine-/ten-stage sequences disagree on whether **cache lookup precedes or follows the authorization pre-filter**. §4.2 is authz-then-cache; §5 is cache-then-authz. A cache hit served *before* authz could, in the worst implementation, return content for a doc the tenant is not allowed to see (mitigated but not eliminated by I1/I2/I9 + "authorization revalidation on every hit" in M4 gate 1 and the `CACHE_STALE_RISK` path). The *leak is contained* by two-phase authz, but the **ordering is ambiguous**, which is exactly what a security-sensitive pipeline must not be.
*Proposed wording — make §4.2 canonical and fix §5 to match:* "authenticate → derive tenant → **authorization pre-filter** → cache lookup (ACL-fingerprint-checked; every hit revalidated against current ACLs) → deterministic budget selection → hybrid retrieval → independent post-retrieval authorization verify → bounded context build → local generation with citations → evidence ledger → trace record + failure attribution → response." Add: "Cache lookup **never** precedes the authorization pre-filter; the post-verify is independent of both." Reconcile the M4 gate-1 "revalidation on every hit" as defense-in-depth, not the primary ordering.

#### Medium

**M3 — Log sink and uv package-cache destination not pinned to D: (Item 3).**
*Sections:* §12 (`env.ps1`), §11 tree (lines ~475–476), §10 (Z-rules).
*Problem:* `UV_CACHE_DIR`/`HF_HOME`/`TMP`→D: and `data\`/`reports\` on D: are explicit and correct. But (a) the **application log** sink (uvicorn / structured JSON log file) has no stated path; default uvicorn logging goes to stderr, but any *file* log is unspecified — risk it lands on C:; (b) the **uv package cache** is implied ("uv cache on D:") but not shown as an `UV_CACHE_DIR` assert in `setup.ps1`. Also `npx`/npm MCP servers write to the **npm cache on C:** by default (outside the project, but grows C:).
*Proposed wording (§12):* "Log **file** sink is `D:\rag-platform\reports\logs\` (stderr default; file logging opt-in and always on D:). `setup.ps1` asserts `$env:UV_CACHE_DIR` and `$env:HF_HOME` resolve under `D:\rag-platform` and **fails loud** if either points at C:. Document that MCP/`npx` servers use the default npm cache on C: (not project artifacts) and are excluded from the D:-only invariant."

**M4 — Verification steps for FTS5 / Qdrant / egress / Python-compat lack concrete commands (Item 6).**
*Sections:* §7.1 gates 3–5, §12 (FTS5 detect R20), §9.3, Phase 0 (line ~522).
*Problem:* All four checks are *planned* and FTS5 is *measured available* here, but the plan specifies no runnable command, so "verify" is not reproducible by another operator. `qdrant-client` is (correctly) not installed yet, so its persistence/filter test is genuinely a Phase 0 activity — but the exact test is unnamed.
*Proposed wording (add a §12 "Verification commands" block):*
- *FTS5:* `python -c "import sqlite3; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')"` → must not raise; else dense-only fallback (R20).
- *Qdrant persistence:* after `uv sync`, start Qdrant-local on `D:\rag-platform\data\qdrant`, insert labeled fixture, **restart process**, assert same count + payload filter returns exactly the expected ID set (gate 4).
- *Egress:* run suite with `EGRESS_MODE=off` under a socket-instrumenting guard (e.g. `pytest` + a conftest that fails on any outbound `connect()` to a non-loopback address) → assert **zero** outbound sockets (T-ADV-09, I3).
- *Python compat:* `python -c "import fastapi, pydantic, httpx, pypdf, psutil"` under the v2.14-correct interpreter; on `ImportError`/build failure trigger R7 contingency (`uv python install 3.12`, `UV_PYTHON_INSTALL_DIR`→D:).

**M5 — Synthetic-eval self-bias mitigation is thin (Item 7).**
*Sections:* §9.3 (line ~398), R19 (line ~497).
*Problem:* Author-bias risk is rated **High-likelihood** and honestly disclosed (good), but the only mitigation is a frozen dev/test split authored by the same author. That controls *overfitting to splits*, not *systematic favoritism of our own heuristics in the gold set itself* (a paraphrase-blind author will author paraphrase-blind gold). No difficulty stratification, no blind holdout, no second-author/independent pass.
*Proposed wording (R19 + §9.3):* "Beyond the frozen dev/test split: (a) stratify the gold set by evidence type (verbatim / paraphrase / multi-hop / unanswerable) and **report per-stratum metrics**, so a heuristic that only wins on verbatim evidence is visible; (b) hold out a **blind test split never used for threshold tuning** (thresholds tuned on dev only); (c) mark results as **self-consistency, not external validity** in every report; (d) treat the later real-doc set (D10/D11 path) as the only external-validity check."

**M6 — M1 steady-state RAM gate vs live free RAM not reconciled (Item 8).**
*Sections:* §7.1 gate 9 (line ~299), §10.1 (lines ~419–424), §10.3 stop-conditions (line ~438).
*Problem:* GPU/RAM *assumption discipline is strong* (I13, §10.3 "never assumed", measured `ollama ps`, measured free-RAM + swap stop-conditions) — Item 8 is essentially met. Residual: gate 9 asserts "steady-state app RAM ≤1.5 GB (provisional)" and "generation spike leaves ≥3 GB free." Live machine has **1.4 GB free now** (browser/VS Code/agents running). A CPU-generation worst case (§10.1 "~7–9 GB") cannot coexist with ≥3 GB free under current load. The stop-conditions correctly *abort*, but the gate could be evaluated as `blocked` rather than `failed`, and the ≤1.5 GB target may be unmeasurable while other apps hold RAM.
*Proposed wording (gate 9):* "Steady-state app RAM ≤1.5 GB (provisional) **measured with a documented, reproducible background-app baseline recorded alongside the number**; generation spike leaves ≥3 GB system free **or the gate is marked `blocked` (not `failed`) when baseline free RAM is already below the floor — BG-1 stop-conditions govern, and the budget is revised with measured data (§10.1), never with an assumed GPU offload.**"

#### Low

**L1 — `git` MCP server still targets a Kio repo.** Correct owner, misleading for `D:\rag-platform`. Fold the retarget into the D8 `git init` decision (do not init git in strict mode). No action now.

**L2 — `dbhub` MCP server targets a Kio DSN on C:.** Irrelevant to this task; harmless. Leave as-is (not part of the reconnect fix).

---

### Exact document sections requiring correction (consolidated)

| Section | Line (approx) | Issue | Change type |
|---|---|---|---|
| v2 §10.3 title | ~431 | H1 | Reword timing; move BG-1 to pre-M1 gate |
| v2 §15 Phase 8 | ~530 | H1 | Relocate BG-1 entry |
| v2 §7.1 gate 7 | ~297 | H1 | Reference pre-M1 BG-1 gate |
| v2 §12 (setup.ps1) | ~484 | H2 | Add snapshot/`blocked` handling (keep ≥5 GB threshold) |
| v2 §4.2 stage list | ~103 | H3 | Keep canonical (authz before cache) |
| v2 §5 pipeline | ~232 | H3 | Reorder to authz → cache; add "cache never precedes authz" |
| v2 §11 tree / §12 | ~475, ~484 | M3 | Pin log-file sink to D:; assert UV_CACHE_DIR/HF_HOME on D: |
| v2 §12 (add block) | new | M4 | Concrete verify commands (FTS5/Qdrant/egress/compat) |
| v2 §9.3 + R19 | ~398, ~497 | M5 | Difficulty stratification + blind holdout + self-consistency label |
| v2 §7.1 gate 9 | ~299 | M6 | Reproducible baseline + `blocked` semantics |

No correction is required to `RAG_Platform_Master_Plan.md` (v1) — it is preserved verbatim (SHA256 verified). The strategy review and plan diff are internally consistent with v2 and require no change; H1/H3 are v2-internal contradictions surfaced *by* the diff's own §10.3/§15 split.

---

### Unresolved unknowns (explicit)

1. **Exact OpenCode internal timeout field** that was bypassed by the flat config shape was inferred from behavior + docs (V2 object `mcp.timeout`), not read from source. The fix is verified empirically (all 11 green) but the precise broken key is a documented inference, not a source-confirmed fact.
2. **C: free-space volatility driver** not isolated (OneDrive sync vs pagefile vs temp). Measured range 4.7–12.4 GB; cause not attributed to a single process.
3. **`qdrant-client` persistence/filter behavior on this machine** unverified (package not installed — correctly deferred to Phase 0). Its local-mode filter API is assumed per v1 §2.1, not measured here.
4. **Live GPU-offload state of `qwen3:8b`** unknown (never loaded during preflight). §10.3 correctly defers this to BG-1 with `ollama ps` proof — no assumption made here.
5. **Whether the flat→V2 config migration changed any tool behavior** beyond reconnect (e.g., catalog refresh timing) was not A/B-tested; only end-state connectivity was verified.
6. **npm/npx MCP servers grow the C: npm cache** (outside the project). Whether the user considers that a D:-only violation is a policy question the plan does not currently answer (raised in M3).

---

## Readiness verdict

**Ready after corrections.**

The plan is structurally sound, honestly scoped, zero-cost/D:-compliant in intent, and its security and determinism discipline (two-phase authz, ledger honesty I13, no-GPU-offload, zero-egress I3/I4) is strong and correctly stated. The v1 plan is preserved byte-identical. MCP is fully reconnected and verified (all 11 green).

Three **High** items must be corrected before Phase 0 to avoid rework and a security-ordering ambiguity: **H1** (BG-1 timing), **H2** (C:-guard flakiness handling — threshold unchanged), **H3** (cache-vs-authz ordering). Four **Medium** items (M3–M6) should be corrected to make gates reproducible and self-bias visible. None is Critical; none leaks unauthorized content; none is a zero-cost or D:-only violation.

**Recommended next step (not started — awaiting D1 approval):** apply the wording corrections above to `RAG_Platform_Master_Plan_v2.md`, then begin Phase 0 under the D-decision gate.

---

*End of preflight audit.*
