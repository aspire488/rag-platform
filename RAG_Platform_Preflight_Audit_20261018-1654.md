# RAG Platform v2 — Final Preflight Audit (Evidence-Based, Read-Only)

**Report file:** `D:\rag-platform\RAG_Platform_Preflight_Audit_20261018-1654.md`
**Created:** 2026-10-18, ~16:55 +05:30
**Mode:** Strictly read-only planning/diagnostic audit. No application code written, no packages or models installed or downloaded, no services or language servers started or restarted, no Git state touched, no OpenCode/MCP/LSP configuration or environment variables modified, no files written to C:, no plan documents edited.
**Audited documents (all read completely):**
- `D:\rag-platform\RAG_Platform_Master_Plan.md` (v1.0) — SHA256 `7E739904BBB46B3A57CAB94012DF767BEA3C495F53B02560F9AD057052EC9FDD` (start and end of audit — byte-identical, untouched)
- `D:\rag-platform\RAG_Platform_Master_Plan_v2.md` (v2.0) — SHA256 `AF4114BDD50CD3430E682E76F2D853437B13DFBBBEA81FE1F9D476D64F9486E5` (start and end — unchanged)
- `D:\rag-platform\RAG_Platform_Strategy_Review.md` (v1.0) — SHA256 `6E3D9347C60A06452998FEAEA2DC643F96FA8943AA6B77EA660FD46365D5E2AC` (start and end — unchanged)
- `D:\rag-platform\RAG_Platform_Plan_Diff.md` (v1.0) — SHA256 `69BF45D22D360B93E73855D079715A9854492C85F7C633C771A8935332ECF75D` (start and end — unchanged)

**Filename note:** A prior audit report `D:\rag-platform\RAG_Platform_Preflight_Audit.md` (21,542 bytes, written 16:03:11 today by an earlier session) already exists. Per the no-overwrite restriction it was preserved untouched (SHA256 `9371C2F5C2C1DDF462BB74544DB40C0F5EDEA966A38EE7E661FC071668B31389`) and this report uses a unique timestamped filename. Where the prior report contains measurements from its own session, they are cited as historical evidence and clearly labeled.

---

## 1. Executive summary

**Readiness verdict: `READY AFTER CORRECTIONS`** (see §12).

The revised plan (`RAG_Platform_Master_Plan_v2.md`, with its companions) is internally disciplined, honest about uncertainty, zero-cost-compliant, and D:-only in intent. Its security invariants (I1–I13), kill-switch philosophy, and evidence-honesty rules (I13, §9.4, Appendix B) are strong. No Critical finding was made: nothing in the documents indicates data leakage, destructive behavior, or a fundamental violation of a non-negotiable constraint.

Three **High** findings require wording corrections before Phase 0:

1. **H-1 — BG-1 sequencing contradiction.** v2 §10.3 says BG-1 runs "once at M1 start"; §15 Phase 8 places it as the *last* M1 phase and §7.1 gate 7 requires its report. The model verdict (D3) therefore arrives after Phases 3–7 have already been built against an unvalidated model assumption. Resolution: split BG-1 into a model-only part (after Phase 0) and fold its pipeline-unavailability step into Phase 6.
2. **H-2 — C: ≥5 GB guard vs. a moving target.** Current observed free space on C: is **9.93 GB** (16:41 today) so the guard would pass *now*, but the same day measured ~4.6–4.7 GB (v1 §2.1; prior audit), where it would fail. The threshold must not be weakened; the fix is snapshot-and-`blocked`-semantics plus a D:-path assert.
3. **H-3 — Pipeline stage-ordering contradiction.** v2 §4.2 orders `authz_filter → cache_lookup`; §5 orders `cache lookup → authorization pre-filter`. A cache hit served before the authorization pre-filter is the exact surface invariant I1 exists to protect. The leak is contained by two-phase authorization and cache revalidation (M4 gate 1), but the ordering ambiguity must be resolved in one canonical pipeline before implementation.

**MCP diagnosis (Task A): all eleven previously-failing servers are `not currently failing`.** This is confirmed by three independent evidence sources: (a) the current `opencode.json` (written 16:23:09 today) contains the repaired V2-shape configuration with universal filesystem roots and unpinned Git; (b) the OpenCode log records all eleven servers `mcp connected` at 16:23 (+05:30) with healthy tool counts and zero MCP errors since; (c) this very session holds live tool catalogs from all eleven servers, matching those counts. The historical 15:29 failures ("Request timed out" for 10 servers; DNS failure for github) are consistent with the pre-repair configuration and stale session state; the precise internal mechanism remains *probable*, not re-confirmable without source inspection. Additionally, **three OpenCode plugins fail to load on every run** (`augmentation.ts`, `event-logger.ts`, `@renjfk/opencode-model-fallback`) — an OpenCode tooling issue, not an MCP or plan issue, reported here as an observation only.

Five **Medium** findings: unpinned log-sink/uv-cache asserts for D:-only (M-1), missing concrete verification commands for FTS5/Qdrant/egress/compat (M-2), thin synthetic-eval self-bias mitigation (M-3), the ≥3 GB free-RAM gate vs. today's 2.02 GB free (M-4), and the HF-hub offline-flag gap plus tooling-egress scope statement (M-5).

---

## 2. Audit scope and read-only methods

**Scope.** Task A: diagnose the eleven reported MCP errors against *current* configuration and logs (inspect, never repair). Task B: audit the four planning documents for correctness, internal consistency, security, feasibility, sequencing, and adherence to zero-cost and D:-only requirements (checks 4.1–4.10).

**Methods used (all non-mutating):**
- Directory listings and file reads (`D:\rag-platform`, `C:\Users\joelj\.config\opencode`, OpenCode data dirs).
- SHA256 hashing of the four planning documents (start and end of audit) and of configuration files.
- OpenCode log inspection: `C:\Users\joelj\.local\share\opencode\log\opencode.log` (26.6 MB, last write 16:33:45) via pattern search; no log files were written or rotated.
- Configuration diffing: current `opencode.json` vs. `.bak-20261018-154937` and older backups (content comparison only).
- Executable resolution via `Get-Command` (PATH lookup only — **no server, language server, or MCP process was launched**).
- Environment-variable **presence checks only** (`[bool]$env:GITHUB_PAT`); values were never read or printed.
- Disk free space via `Get-PSDrive` (no file created on either volume).
- Free RAM via `Get-CimInstance Win32_OperatingSystem` (query only).
- GPU state via `nvidia-smi --query-gpu=...` (query only).
- Ollama state via the read-only local endpoint `GET http://localhost:11434/api/tags` (model inventory only — **no inference, no load/unload, no service control**).
- Python/uv version queries (`python --version`, `uv --version` — version strings only).

**Deliberately NOT run, per restrictions:** `opencode mcp list` or any command that would spawn MCP servers for a fresh handshake; any LSP initialization; FTS5/Qdrant/e­gress verification snippets (deferred to Phase 0/M1 per plan and task rules); `git init` or any Git mutation; any install, download, or service control. Where a check would have required such actions, it is marked **unverified**.

---

## 3. Current environment observations and evidence timestamps

All times +05:30, 2026-10-18, unless marked UTC. Evidence source in parentheses.

| # | Observation | Evidence | Timestamp |
|---|---|---|---|
| E1 | Working directory and project root confirmed as `D:\rag-platform` | `Get-Location` | 16:41 |
| E2 | `D:\rag-platform` is **not** a Git repository (`.git` absent) — consistent with "Git state untouched" | `Test-Path D:\rag-platform\.git` → False | 16:47 |
| E3 | Workspace contains exactly: four planning documents, the prior audit report, and a pre-existing `.ruff_cache\` directory (LastWriteTime 16:32:52 — created before this audit session began at 16:41; **not created by this audit**) | Directory listing | 16:47 |
| E4 | **C: 9.93 GB free; D: 194.49 GB free** | `Get-PSDrive` | 16:41:01 |
| E5 | Free RAM **2.02 GB** of 15.23 GB total | `Get-CimInstance Win32_OperatingSystem` | 16:44 |
| E6 | Ollama API reachable, HTTP 200; models: `qwen3:latest` 4.87 GB, `qwen3:8b` 4.87 GB, `qwen2.5-coder:7b` 4.36 GB (identical sizes of the two qwen3 entries are consistent with alias assumption A1; digests not compared — unverified) | `GET localhost:11434/api/tags` (read-only) | 16:44 |
| E7 | GPU present: RTX 3050 6 GB Laptop, 0 MiB used, driver 610.88 | `nvidia-smi` query | 16:44 |
| E8 | Python 3.14.5 (`C:\Python314\python.exe`), uv 0.11.32 | version queries | 16:45 |
| E9 | Current `opencode.json` (8,865 bytes, **LastWriteTime 16:23:09**) contains: `mcp.servers` V2 shape; `mcp.timeout = {startup: 60000, catalog: 30000}`; **filesystem roots `C:\Users\joelj`, `C:\`, `D:\`** (the reported universal roots are present — verified, not assumed); **git server with no repository pin** (repository-independent — verified); **8 LSP entries** | File read | 16:42 |
| E10 | Config differs from newest backup `opencode.json.bak-20261018-154937` (5,828 bytes, 11:32:22); older backup `bak-20260808-115813` contains the pre-repair flat `mcp` layout without `servers` | Content comparison | 16:46 |
| E11 | Log: **15:29:08** (09:59:08 UTC, run `ab3416ea`) — `mcp connect failed … status.error="Request timed out"` for filesystem, sequential-thinking, git, dbhub, memory, playwright, firecrawl, serena, chrome-devtools, context7 (some per-directory duplicates). **github is absent from the timeout list** (consistent with the prior audit's DNS diagnosis for github specifically) | Log search | historical |
| E12 | Log: **16:23:29–16:23:42** (10:53 UTC, run `9d51c35a`) — `mcp connected` for **all eleven servers** with tool counts: git 12, github 46, dbhub 2, sequential-thinking 1, filesystem 14, playwright 25, memory 9, chrome-devtools 32, context7 2, firecrawl 28, serena 29; connection spans 5.7–17.5 s (inside the 60 s startup timeout) | Log search | 16:23 |
| E13 | Log: **zero MCP errors after 16:23**; tail shows routine watcher activity at 16:48 | Log tail | 16:48 |
| E14 | This session exposes live tool catalogs from **all eleven servers** with counts matching E12 — direct in-session proof of current health | Session tool catalog | session start |
| E15 | All configured MCP/LSP executables resolve on PATH without launching: `npx`, `node`, `uvx`, `python`, `git`, `rust-analyzer`, and the seven npm language servers; `serena.exe`, dbhub `server\index.js`, and `dbhub.toml` all exist | `Get-Command` / `Test-Path` | 16:42 |
| E16 | Credential **presence** (values never read): `GITHUB_PAT` set; `FIRECRAWL_API_KEY` set | Env presence check | 16:42 |
| E17 | Log: three plugins fail to load on runs `f5e795f5` and `9d51c35a` (current): `plugins\augmentation.ts`, `plugins\event-logger.ts`, `@renjfk/opencode-model-fallback` — "Plugin must export a default definition with an id" | Log search | 16:23 |
| E18 | Log (historical, pre-repair): "all LSPs are disabled" at 10:29–10:30 (05:29–05:30 UTC, runs `9592d0e6`, `e1415391`). No LSP-disabled or LSP-error lines after the 16:23 repair; no explicit "LSP started" lines found either | Log search | historical/current |
| E19 | `opencode.jsonc` exists but contains only `$schema` — no competing configuration; which file is authoritative is not re-verified | File read | 16:46 |
| E20 | Historical (prior audit session, ~16:03): FTS5 available (`MATCH` works, sqlite 3.50.4); `sentence-transformers`, `torch`, `fastapi`, `pypdf`, `psutil`, `pytest`, `httpx`, `pydantic`, `networkx` importable from system Python 3.14; `qdrant-client` and `pip-audit` absent; C: measured 4.6–4.7 GB free earlier today; torch CPU-only `2.13.0+cpu` | Prior audit report | historical |

---

## 4. MCP diagnosis table (Task A — all eleven servers)

**Legend for confidence:** `confirmed` = supported by direct current evidence; `probable` = best explanation consistent with multiple evidence points but not directly re-verifiable read-only; `unknown` = insufficient evidence. Every server listed below is **configured (confirmed)**, **resolvable (confirmed, E15)**, and **connected in this session (confirmed, E12/E14)**.

| MCP | Current configuration evidence | Existing error evidence | Diagnosis and confidence | Essential for this audit? | Smallest safe next action |
|---|---|---|---|---|---|
| `filesystem` | `opencode.json` mcp.servers: local stdio, `cmd /c npx -y @modelcontextprotocol/server-filesystem C:\Users\joelj C:\ D:\` — **universal roots confirmed present** (E9) | 15:29 `Request timed out` (E11); 16:23 `mcp connected`, tools=14 (E12); live 14 tools in session (E14) | **Not currently failing (confirmed).** Historical failure: probable pre-repair config-shape/timeout issue in stale session `ab3416ea` | Yes — general file access (native tools were sufficient; server available) | None |
| `git` | Local stdio, `uvx --with mcp<2.0.0 mcp-server-git` — **no repository pin** (repository-independent confirmed, E9) | 15:29 timeout (E11); 16:23 connected, tools=12 (E12) | **Not currently failing (confirmed).** Historical: probable same shared cause | No — `D:\rag-platform` is not a Git repo (E2) | None now; align any retarget with D8 (`git init`) decision later |
| `memory` | Local stdio, `cmd /c npx -y @modelcontextprotocol/server-memory` | 15:29 timeout; 16:23 connected, tools=9 | **Not currently failing (confirmed)**; historical probable shared cause | No | None |
| `sequential-thinking` | Local stdio, `cmd /c npx -y @modelcontextprotocol/server-sequential-thinking` | 15:29 timeout; 16:23 connected, tools=1 | **Not currently failing (confirmed)**; historical probable shared cause | No | None |
| `context7` | Local stdio, `cmd /c npx -y @upstash/context7-mcp` (network client by design) | 15:29 timeout; 16:23 connected, tools=2 | **Not currently failing (confirmed)**; historical probable shared cause | No — docs lookup only | None |
| `playwright` | Local stdio, `cmd /c npx -y @playwright/mcp@latest` | 15:29 timeout; 16:23 connected, tools=25 | **Not currently failing (confirmed)**; historical probable shared cause | No | None |
| `serena` | Local stdio, `C:\Users\joelj\.local\bin\serena.exe start-mcp-server`; exe exists (E15) | 15:29 timeout; 16:23 connected, tools=29 | **Not currently failing (confirmed)**; historical probable shared cause | No | None |
| `chrome-devtools` | Local stdio, `cmd /c npx -y chrome-devtools-mcp@latest --no-usage-statistics --isolated --headless`; env `CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS=1` (non-secret) | 15:29 timeout (two per-directory entries); 16:23 connected, tools=32 | **Not currently failing (confirmed)**; historical probable shared cause | No | None |
| `github` | Remote HTTP `https://api.githubcopilot.com/mcp/`, `Authorization: Bearer {env:GITHUB_PAT}`, `oauth: false`; `GITHUB_PAT` present (E16, value not read) | **Absent from the 15:29 timeout list**; prior audit recorded `getaddrinfo ENOTFOUND` earlier in the day; 16:23 connected, tools=46 | **Not currently failing (confirmed).** Historical: probable transient DNS, distinct from the ten timeouts — no common cause can be claimed for github | No | None |
| `dbhub` | Local, `node …\dbhub\server\index.js --transport stdio --config …\dbhub.toml` (both files exist); env `DBHUB_DSN` points to an **unrelated Kio project SQLite DB on C:** (OneDrive path) — path presence only, no secret | 15:29 timeout; 16:23 connected, tools=2 | **Not currently failing (confirmed).** DSN scope: stale/misleading for this project (Low finding L-1), harmless | No | None; consider later retarget/cleanup outside this project |
| `firecrawl` | Local stdio, `cmd /c npx -y firecrawl-mcp@latest`; env `FIRECRAWL_API_KEY` present (E16, value not read) | 15:29 timeout; 16:23 connected, tools=28 | **Not currently failing (confirmed)**; historical probable shared cause | No | None |

### 4.3 Shared-cause analysis

- **A stale OpenCode session: strongly supported for the historical failures.** The 15:29 failures occurred in run `ab3416ea` under the pre-repair configuration; ten servers timed out within ~1.6 seconds of each other (15:29:08.203–15:29:09.864) — a simultaneous reaping pattern, not eleven independent crashes. The current configuration file (written 16:23:09) was followed by successful connections 20–33 seconds later in a fresh run, and no failures have occurred since.
- **A common command/path-resolution issue: refuted.** All executables and scripts resolve (E15); two servers in the prior session also booted cleanly by hand.
- **Missing credentials: refuted.** Both referenced credentials are present (presence-only check).
- **Invalid configuration schema: probable contributor, historically.** The pre-repair flat `mcp` layout (E10) plus the 60 s startup timeout now in force is the best documented explanation of the timeouts; this matches the prior audit's root-cause claim, but the precise internal field semantics were not re-verified against OpenCode source in this audit — labeled *probable*, not confirmed.
- **A shared process/runtime failure: not supported.** Spans and tool counts are healthy per server; failure modes differed (github = DNS, absent from the timeout wave).
- **Individual server failures: none currently observable.** One historical quirk (prior audit: dbhub "unknown message ID") cleared and did not recur.
- **Conclusion:** the simultaneous failure is best explained by *one shared environmental cause* (stale session + pre-repair config shape), with github's DNS error as an independent transient. Multiple servers failing at once is **not** here treated as proof of a common cause by itself — the timing pattern, config archaeology, and clean re-connects are what support the conclusion.

---

## 5. LSP registration and health evidence

The eight reported integrations are **confirmed present** in the current `opencode.json` (E9): `typescript`, `python` (pyright-langserver), `json`, `yaml`, `markdown`, `css`, `html`, `rust` (rust-analyzer). Distinguishing the four states required by the task:

| State | Evidence | Status |
|---|---|---|
| Registered in configuration | 8 entries read from `opencode.json` at 16:42 | **Confirmed** |
| Executable appears available | All 8 resolve via PATH (`typescript-language-server`, `pyright-langserver`, `vscode-json-language-server`, `yaml-language-server`, `vscode-markdown-language-server`, `vscode-css-language-server`, `vscode-html-language-server` in npm global; `rust-analyzer.exe` in `.cargo\bin`) — resolved without launching | **Confirmed** |
| Previously initialized successfully | Log shows `all LSPs are disabled` at 10:29–10:30 (pre-repair runs); **no** "LSP started" or LSP-error entries exist after the 16:23 repair | **Unverified** |
| Currently verified healthy | LSPs spawn lazily on matching file opens; this audit opened only `.md` files and no LSP spawn/startup evidence appears in the log; no server was initialized (forbidden) | **Unverified** |

Not interchangeable, as instructed: the configuration and executables are verified; runtime health is **not** verified and must not be claimed. The absence of "all LSPs are disabled" lines after 16:23 is *consistent with* the restored config but is not positive proof of initialization.

---

## 6. Prioritized findings

Severity definitions per task: **Critical** = likely data leakage, destructive behavior, or fundamental constraint violation. **High** = likely to block implementation or invalidate a major acceptance gate. **Medium** = material reliability/evaluation/maintainability/resource concern. **Low** = clarity/polish.

### Critical
**None.** No document content indicates data leakage, destructive behavior, or a fundamental violation of Z1–Z12. The two-phase authorization design (I1), server-derived identity (I2), unsuppressable abstention (I12), advisory-never-gates rule (I11), and ledger honesty (I13) are all correctly specified.

### High

**H-1 — BG-1 benchmark sequencing contradiction.**
- Sections: v2 §10.3 title ("run once at M1 start") vs. v2 §15 Phase 8 ("BG-1 (§10.3) → feeds D3") and §7.1 gate 7 ("BG-1 benchmark report … model verdict recorded").
- Why it matters: as scheduled, Phases 3–7 (embeddings, ingestion, retrieval, generation client) are built before the model verdict (D3) exists; if the verdict is `too-heavy`/`too-slow`, generation defaults, `num_ctx`, and timeout tuning are reworked. See §7.1 of this report for the full dependency trace.
- Fix: during planning — split BG-1 (see §9, C-1).
- Correction type: planning-only wording change; the benchmark itself remains a later implementation-time test.

**H-2 — C: ≥5 GB guard asserts against a volatile value.**
- Sections: v2 §12 ("`setup.ps1` asserts: C: free space ≥ 5 GB (fail loud)"); v1 R6/Phase 0 (carried unchanged).
- Evidence: current C: free = **9.93 GB** (E4, 16:41) → guard would pass now; v1 §2.1 and the prior audit measured **~4.6–4.7 GB** earlier the same day → guard would have failed then.
- Why it matters: a hard fail-loud assert on a value that swings ≥5 GB within one day yields nondeterministic setup outcomes.
- Fix: during planning — keep the threshold; add snapshot/`blocked` semantics (see §9, C-2). Do **not** weaken the guard.

**H-3 — Request-pipeline ordering contradiction (§4.2 vs. §5).**
- Sections: v2 §4.2 ("Stages: `authn → authz_filter → cache_lookup → retrieval → post_verify → context_build → generation → ledger → response`") vs. v2 §5 ("authenticate → derive tenant from credential → cache lookup (fingerprint-checked) → authorization pre-filter → …"). §4.2 is authz-then-cache; §5 is cache-then-authz. §4.2/§7.3/M3-gate-5 also reference "nine stages" while §5's prose enumerates a different number of steps (budget selection and trace recording appear in one list but not the other).
- Why it matters: a cache hit served before the authorization pre-filter is precisely the surface I1 protects; M4 gate 1 revalidation and the `CACHE_STALE_RISK` path contain the leak in the worst implementation, but a security-sensitive pipeline must have exactly one canonical ordering. Ambiguity here would propagate into M2/M3 code and the trace schema.
- Fix: during planning — one canonical pipeline with mandatory gates vs. advisory stages marked (see §9, C-3).

### Medium

**M-1 — D:-only gaps: log sink unspecified; no setup assert that cache env vars actually resolve under D:.**
- Sections: v2 §10.2/§12 (`env.ps1` sets `UV_CACHE_DIR`/`HF_HOME`/`TMP` → D:); §11 tree; Z9.
- Why it matters: `env.ps1` is dot-sourced manually; if any script or shell runs uv/HF operations without it, caches silently land in the default C: locations (uv default cache under the user profile; HF default `~\.cache\huggingface`), growing C: — Z9 violation without any error. The application *file*-log sink is also unspecified anywhere.
- Fix: planning-time wording (see §9, C-4). Note: npm/npx MCP servers also grow the npm cache on C: — that is tooling, not project artifacts; the plan should say so explicitly.

**M-2 — FTS5/Qdrant/egress/Python-compat verification steps lack concrete reproducible commands.**
- Sections: v2 Phase 0 ("FTS5 detection (R20)"; "Python 3.14 compat check (R7)"); §7.1 gates 3/4/5/8; R20.
- Why it matters: "detect" and "verify" are planned but no runnable command is specified, so another operator (or a later session) cannot reproduce the checks identically. FTS5 was historically measured available (E20) but that must be re-verified in the target venv at Phase 0 — the prior measurement used the system interpreter.
- Fix: planning-time — add a verification-commands block (see §9, C-5). Commands are **not** run during this audit.

**M-3 — Synthetic-eval self-bias mitigation is thin relative to R19's High likelihood.**
- Sections: v2 §9.2 (planted structures), §9.3 ("Gold authored by the same author as corpus (self-consistency risk…)"), R19, §4.1 success criteria (unsupported-claim recall ≥ provisional 80%).
- Why it matters: the planted-unsupported set is authored to be non-contained, which is exactly the containment classifier's rule — the ≥80% target is partly self-fulfilling; the same author's paraphrase habits shape both corpus and gold. Frozen splits control tuning overfit, not systematic favoritism of the implementation's own heuristics.
- Fix: planning-time wording (see §9, C-6); remains zero-cost and locally runnable.

**M-4 — M1 gate 9 RAM floor vs. observed 2.02 GB free RAM.**
- Sections: v2 §7.1 gate 9 ("generation spike leaves ≥3 GB system RAM free or budget revised"); §10.1 ("CPU-generation worst case ~7–9 GB"); §10.3 step 6 stop conditions.
- Evidence: 2.02 GB free now (E5). A 7–9 GB generation spike cannot leave ≥3 GB free unless foreground apps close.
- Why it matters: without `blocked`-vs-`failed` semantics and a documented background baseline, the gate is unmeasurable under normal laptop use and may be misreported as failure (violating neither Z8 nor honesty, but muddying the record).
- Fix: planning-time wording (see §9, C-7). BG-1's stop conditions already govern the benchmark itself.

**M-5 — Egress guarantee needs an explicit tooling-scope statement and HF offline flags.**
- Sections: v2 I3/I4, T-ADV-09 (M1 gate 8), §6 ("sentence-transformers … internet only if cache missing" carried from v1 §5), §10.2/§12.
- Why it matters: (a) the socket-instrumented suite (T-ADV-09) is the real guarantee — a config flag alone is not; the plan is correct to have both, but the *scope* should say the guarantee covers the platform process, while dev tooling (npx-based MCP servers such as `firecrawl`, which are network clients by design, and `npx -y` first-run package fetches) is outside it. (b) HF/transformers libraries can perform network *update checks* even with a warm cache unless `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` are set; the plan's `env.ps1` does not set them, which is a latent T-ADV-09 failure.
- Fix: planning-time wording (see §9, C-8).

### Low

**L-1 — dbhub DSN still targets an unrelated Kio project DB on C:.** Harmless for this project; candidate for later cleanup via config change (out of scope in this read-only audit). No secret value is exposed by the DSN.

**L-2 — `opencode.json` and near-empty `opencode.jsonc` coexist.** Only the `$schema` key is in the JSONC; current behavior indicates `opencode.json` is authoritative. Ambiguity note only; no change made.

**L-3 — Plugin load failures (OpenCode tooling, not the plan).** `augmentation.ts`, `event-logger.ts`, and `@renjfk/opencode-model-fallback` fail to load in the current run ("Plugin must export a default definition with an id"). Effect: the model-fallback plugin configured in `opencode.json` is inert. This does not affect the RAG plan or the eleven MCP servers. Smallest safe action: none during this audit (configuration changes are forbidden); owner may fix or remove the plugins in a dedicated session.

**L-4 — `.ruff_cache\` directory exists in the project root** (16:32:52, pre-dating this audit). Not a planning document; should eventually be gitignored (v2's `.gitignore` plan covers caches). Observation only.

---

## 7. Detailed findings for checks 4.1–4.10

### 7.1 (4.1) BG-1 benchmark sequencing

**Dependency chain traced end-to-end:**

| Step | Where defined | Needs model? | Notes |
|---|---|---|---|
| Phase 0 (scaffold, uv, FTS5 detect, Python compat, C: assert) | v2 §15 | No | Environment inspection happens here |
| Phases 1–2 (config, SQLite/Qdrant storage) | v2 §15 | No | |
| Phase 3 (MiniLM embeddings) | v2 §15 | No | MiniLM, not Ollama |
| Phase 4 (ingestion) | v2 §15 | No | |
| Phase 5 (hybrid retrieval) | v2 §15 | No | Retrieval pipeline available here |
| Phase 6 (Ollama client, abstention, Z11 degradation tests) | v2 §15 | **Yes (first contact)** | |
| Phase 7 (API + CLI) | v2 §15 | No | |
| Phase 8 (frozen corpus + eval harness + trace recorder + **BG-1**) | v2 §15 | Yes | "feeds D3" |
| Gate M1 (§7.1 gates 5–9: hybrid≥dense, eval E2E, **BG-1 report**, egress, perf) | v2 §7.1 | Yes | |
| D3 (model decision) | v2 §14 | — | "Post-BG-1 model verdict … Defer to BG-1 data" |
| D11 (re-freeze provisional margins) | v2 §14 | — | "after M1 baseline, before M3 tuning" |

**Ambiguity resolved:** the plan contains two irreconcilable placements. §10.3's title ("run once at M1 start") and the Plan Diff §8 table ("BG-1 at M1 start") say *start*; §15 Phase 8 and §7.1 gate 7 place it at the *end* of M1. BG-1 is neither purely a model-only safety benchmark nor a later end-to-end evaluation as written — it is written as both.

**Dependency nuance the documents conflate:** BG-1 §10.3 steps 1–4 (pre-checks, 32-token load test, 512-token sustained test, unload check) require only Ollama + psutil + `nvidia-smi`/`ollama ps`. Step 5 ("Unavailability test … pipeline returns retrieval-only within timeout") requires the *retrieval pipeline* — which exists only after Phase 5–6. One protocol, two different dependency footprints.

**Recommended sequence (do not execute now):**
1. Phase 0 complete → environment verified, deps installed on D:, FTS5 detected, compat confirmed.
2. **BG-1a** (model-only: §10.3 steps 1–4, 7–8) runs next, while foreground load is minimal (today only 2.02 GB RAM is free — see M-4; a `blocked` outcome is legitimate). Verdict feeds **D3** *before* Phase 6 tunes generation defaults. Stop condition: §10.3 step 6 verbatim (free RAM < 2 GB at any sample, swap activity, user-reported sluggishness, >15 min runtime) → abort, unload, record; `qwen2.5-coder:7b` fallback check only per §10.3 step 8.
3. Phases 1–6 proceed; **BG-1b** (unavailability/retrieval-only proof, §10.3 step 5) folds into Phase 6 and satisfies M1 gate 2 — no second benchmark.
4. Gate M1 consumes the BG-1a report (relocated reference in gate 7).

This keeps "no benchmarks during planning" intact (BG-1a starts only after D1 approval and Phase 0) and makes the model decision precede the code that depends on it.

### 7.2 (4.2) C: disk-space guard

- **Current observed free space:** 9.93 GB on C: (16:41, `Get-PSDrive`); D: 194.49 GB.
- **Plan's exact requirement:** v2 §12 — "`setup.ps1` asserts: C: free space ≥ 5 GB (fail loud)"; carried from v1 R6 ("assert C: free-space check in `setup.ps1` (fail loud)").
- **Would the guard pass now?** **Pass** (9.93 ≥ 5). **Would it have passed earlier today?** **Fail** (~4.6–4.7 GB per v1 §2.1 and prior audit) — the figure was real then; both readings are true at their timestamps. The guard is currently *passing but unstable*.
- **Which operations genuinely need C::** none of the project's own steps, *provided* `env.ps1` is dot-sourced first. Implicit C: writers remain: (a) Windows TEMP if any uv/pip/HF command runs before `TMP` is redirected; (b) uv's default package cache and (with R7, uv's default Python install dir) if `UV_CACHE_DIR`/`UV_PYTHON_INSTALL_DIR` are unset in a given shell; (c) HF cache if `HF_HOME` is unset; (d) npm/npx cache used by MCP tooling (tooling, not project artifacts). Pre-existing C: content (Python 3.14, Node, Ollama binaries + models, Docker vhdx) is *existing installation*, not new project artifacts, and does not violate Z9 by presence.
- **Safe correction:** keep the ≥5 GB threshold untouched; add a free-space snapshot to the setup report; on assert failure, record the observed value and mark the run `blocked` (re-runnable) rather than a hard fault; add asserts that `UV_CACHE_DIR`, `HF_HOME`, and `TMP` resolve under `D:\rag-platform` (see §9, C-2/C-4). No file was created on C: during this audit to test anything.

### 7.3 (4.3) D:-only storage audit

| Path category | Planned location | Where stated | Gap? |
|---|---|---|---|
| Python interpreter (R7 fallback) | `UV_PYTHON_INSTALL_DIR` on D: | v2 §12, R7 | None |
| Virtual env (`.venv`) | `D:\rag-platform\.venv` | v2 §11 | None |
| uv cache / downloads | `UV_CACHE_DIR` → D: | v2 §10.2/§12 (`env.ps1`) | **No setup.ps1 assert** (M-1) |
| `HF_HOME` / MiniLM cache | copied C:→D:, `HF_HOME` project-local (D2) | v2 §10.2, D2 | No assert (M-1); **missing `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`** (M-5) |
| Temp dirs / test artifacts | `TMP` → D: | v2 §10.2 | Only effective when `env.ps1` is dot-sourced first — ordering not stated |
| Qdrant local DB + WAL | `data\qdrant\` | v2 §11 | None |
| SQLite DBs + WAL | `data\sqlite\` | v2 §11 | None |
| Traces | `data\traces\`, retention-capped (R21) | v2 §11, Z9 | None |
| Logs | unspecified | — | **Gap** (M-1): file-log sink has no stated path |
| Reports | `reports\` (gitignored) | v2 §11 | None |
| Git repo/worktrees | `D:\rag-platform` at Phase 0 (D8); no project worktrees planned | v2 §14/§15 | Note: OpenCode's global worktree dir `C:\OpenCode-Augmentation\worktrees` is tooling, not this project |
| Datasets / PDFs / fixtures | `datasets\`, `data\corpus\` | v2 §11 | None |
| Scripts writing outside D: | none found; `scripts\*.ps1` operate on project paths | v2 §11–§12 | None found |
| Ruff/pytest caches | default = project dir (D:) | implicit | `.ruff_cache\` already on D: (E3) |

Existing system installs on C: (Python 3.14, Node/npm globals, Ollama + 9.23 GB models, Docker vhdx) are pre-existing and explicitly allowed by the policy framing; `OLLAMA_MODELS` stays on C: unless D4 is approved (v2 §10.2). **Recommendation:** the three env-var asserts plus an explicit log-sink path and an explicit "MCP/npx tooling cache on C: is out of scope" sentence close every implicit-default hole found.

### 7.4 (4.4) Evidence Ledger honesty

The plan handles this well. v2 §4.1.5: "`directly_supported` means *textually present*, not *semantically true*; `inferred` is an overlap heuristic, not logical entailment"; §4.1.2 labels the `inferred` tier "explicitly not entailment; documented as a proxy"; I13 forbids calibrated probabilities and any API field implying stronger semantics; §9.4 prints per-metric limitations ("Containment-based `directly_supported` misses paraphrase support"); §4.1.4 requires each classification to carry its method (`containment | overlap | abstained`); advisory model tiers are off-by-default and never gate (I11, D9). Deterministic containment is never presented as semantic proof anywhere in the four documents. **No correction required**; one optional polish sentence for every report header (§9, C-9): "Deterministic containment is textual presence, not semantic support; `inferred` is an overlap proxy, not entailment; no calibrated probabilities exist in this system (I13)."

### 7.5 (4.5) Failure Observatory ordering and security

Traced lifecycle, v2 as written: authentication → server-derived tenant (I2) → [§4.2: authz pre-filter] → [§5: cache lookup] → budget → hybrid retrieval → independent post-verify → bounded context → generation/abstention (I12) → ledger (I13) → trace + attribution (I10) → response. Verified properties: authorization precedes protected content in generation context (I1, enforced pre-filter *and* independent post-verify — both documents agree on two-phase enforcement); trace recording consumes only post-verified spans and stores IDs/offsets/scores/≤200-char excerpts for authorized spans only (I10, §4.2.6); `CITATION_FAILURE` raises an audit event; logs/traces are tenant-scoped with bounded retention (§4.2.6, R21); error bodies never echo internals (I6); cache hits revalidate ACL (M4 gate 1) and cache access is identity-scoped (M3→M4 dependency); abstention is decided before generation and cannot be overridden by cache, budget, or client flags (I12); advisory classifiers cannot gate anything (I11). **The only defect is the ordering/count ambiguity (H-3).** Proposed single canonical pipeline — see §9, C-3 — with mandatory security gates (authn, identity derivation, authz pre-filter, independent post-verify, abstention, egress guard) distinguished from advisory diagnostics (injection quarantine flags, budget policy, ledger `inferred`, attribution rules, advisory tiers).

### 7.6 (4.6) FTS5 and Qdrant verification

Planned coverage exists at the right milestones: Phase 0 FTS5 detection with dense-only fallback (R20); M1 gate 3 (restart persistence), gate 4 (payload-filter fixture), gate 5 (hybrid ≥ dense-only, else disable FTS5 path); M2 gate 4 (tenant-filter correctness); concurrency in M2 (A10) and M3 gate 4; bounded resources via §10.1/R21 and perf tests. **Missing from the plan:** concrete reproducible commands (M-2); BM25 stable-ordering under score ties (FTS5 rank ties → tie-break by rowid — must be asserted, since Recall/MRR/nDCG require deterministic result order); explicit empty-result and deleted-document/index-consistency tests (the vectorstore adapter's remove path exists in v1 §4 but no consistency test is named). **Allocation:** FTS5 availability + Python compat → Phase 0; persistence-across-restart, filter correctness, hybrid fusion + dense-only fallback, BM25 ordering stability, empty/deleted consistency → M1 integration tests; tenant-filter correctness and concurrency → M2. None of these were run during this audit (M-2's command block is proposed wording only).

### 7.7 (4.7) Egress and zero-cost guarantees

The guarantee is testable through the right mechanism: T-ADV-09 instruments httpx/urllib/sockets in-process and requires zero outbound connections across the suite (M1 gate 8, DoD 3); §8.2 adds startup rejection and a URL-literal source grep; `/health` exposes egress mode. Model/embedding initialization is local (Ollama loopback; MiniLM from copied cache); package/model downloads are Phase-0 setup only under D1; telemetry is structurally absent (hand-rolled modules, no framework); optional providers are documentation-only and never in an acceptance path (Z10, Appendix A; the removed cloud smoke test per Plan Diff §5 reinforces this); retry/fallback logic is local-only by invariant (Z5, I5); no paid provider, account, or trial is required by any gate (verified across §7 gates, §9, Appendix A). **Distinguishing code-assertions vs. network verification:** the plan correctly implements both layers — config assertions (startup rejection) are *necessary but insufficient*; the instrumented suite is the actual verification, and §9.3 honestly states what it cannot prove (future dependency updates). **Two gaps:** (a) scope statement for dev tooling (npx MCP servers are network clients; their traffic is not a platform egress violation and must be labeled as out of scope) and (b) HF offline env flags to prevent hub update-check sockets from failing T-ADV-09 (M-5). Neither weakens the guarantee; both make it honest and passable.

### 7.8 (4.8) Python and dependency compatibility

Specified correctly: Python 3.14 first with uv-managed 3.12/3.13 fallback installed **to D:** (`UV_PYTHON_INSTALL_DIR`) at Phase 0 (§6, §12, R7); pinned `uv.lock`; license review + `pip-audit` at gates; no model downloads without approval (Z6/D3/D10). **Gaps:** (a) "read-only compatibility inspection before installation" is not explicitly ordered — recommend a lock/resolution dry-run (wheel availability for the pinned set on 3.14) *before* any install, so a torch/transformers wheel gap triggers R7 early; (b) "no global package installations" is implied by uv's `.venv` model but never stated as a rule — and is materially important because system Python 3.14 *already has* sentence-transformers/torch importable (E20, historical), which could mask venv-completeness problems during Phase 0 checks; compat checks must run inside the uv venv, never against `C:\Python314`; (c) no explicit fallback if a single non-torch dependency fails to install on 3.14 — recommend: mark the gate `blocked` and retry the *whole* environment under R7 (3.12/3.13 on D:), never a partial/global install. Package installs at Phase 0 are permitted setup-time downloads under D1; no new dependency was installed during this audit.

### 7.9 (4.9) Evaluation bias

Confirmed concerns: the corpus is authored by the same agent/person as the classifier rules; planted unsupported-claim targets are non-contained by construction — the containment tier wins by the author's own rule; frozen dev/test splits (§9.5) control threshold-tuning overfit but not gold-set systematic favoritism; the false-flag counter-set (directly-quotable claims) partially balances but does not include *paraphrase-supported* claims (support present, containment absent) — the exact case where the `inferred` tier and false-"unsupported" risk live; baseline-vs-new comparisons are same-dataset (good, §9.3 principle 3); no external-validity claim is made (good — §9.4 labels self-consistency risk). **Recommendations (zero-cost, local, planning-time):** (1) stratify gold by evidence type (verbatim / paraphrase / multi-hop / unanswerable / conflict-aware — §9.2 already types questions; extend to *evidence-type* strata) and report per-stratum metrics; (2) author a **blind holdout batch after thresholds are frozen**, used once; (3) add paraphrase-supported adversarial variants (gold support exists, verbatim containment does not) to measure false-"unsupported"; (4) require a second review pass over a fixture sample (the machine owner, independent of the classifier author) before freezing dataset IDs; (5) print "self-consistency on authored fixtures, not external validity" in every report header; (6) keep the later real-document set (deferred list) as the only external-validity check.

### 7.10 (4.10) Hardware and milestone feasibility

| Item | Class | Value / note |
|---|---|---|
| 16 GB RAM, 15.23 GB total | **Measured (today)** | E5 |
| Free RAM 2.02 GB at 16:44 | **Measured (today)** | fluctuates with browser/VS Code |
| RTX 3050 Laptop, 6 GB VRAM, idle, driver 610.88 | **Measured (today)** | E7 |
| Torch CPU-only `2.13.0+cpu`, no CUDA toolkit | Measured (prior session — stale until rechecked) | E20 |
| Ollama v0.40.4 up; qwen3:8b / qwen3:latest (4.87 GB each) / qwen2.5-coder:7b (4.36 GB) | **Measured (today, inventory only)** | E6 |
| C: 9.93 GB / D: 194.49 GB free | **Measured (today)** | E4; C: volatile |
| GPU offload state of qwen3:8b | **Unknown** | correctly never assumed (A2/A3, R2, §10.3) |
| tok/s, load time, peak RAM of generation | **Unknown / estimate** | v1 A4 estimate 5–15 tok/s; BG-1 will measure |
| All §10.1 budget rows | Provisional targets | labeled as such in the plan |
| Steady-state ≤1.5 GB RAM | Provisional; plausible at 2.02 GB free | M-4 semantics needed for the spike gate |

Feasibility per milestone when Ollama is unavailable: **M1** — retrieval-only mode is a first-class acceptance gate (gate 2, Z11); eval-lexical tests pass with Ollama off. **M2** — entirely retrieval/authz-side; proceeds fully. **M3** — the ledger needs *generated answers* to classify; with an unusable model, M3 gates must be marked `blocked` (honest-status framework supports this; recommend stating it). **M4** — cache hit/miss and cap-compliance tests proceed; latency A/B and answer-quality delta need generation and would be `blocked` likewise. The BG-1 stop conditions (RAM < 2 GB, swap, sluggishness, >15 min) are the correct safety valves and — with M-4's `blocked`-not-failed semantics — no milestone silently assumes GPU offload, model speed, or free RAM. With today's 2.02 GB free, a CPU-generation spike (7–9 GB) would breach both the 2 GB abort floor and the 3 GB gate floor unless foreground apps close; this is an *operational scheduling* fact, not a plan defect — but it must be recorded as a `blocked` path, not a failure.

---

## 8. Exact document sections requiring corrections

| # | Document | Section / heading | Issue | Change type |
|---|---|---|---|---|
| 1 | `RAG_Platform_Master_Plan_v2.md` | §10.3 title "Safe benchmark protocol BG-1 (run once at M1 start…)" | H-1 | Reword + split BG-1a/BG-1b |
| 2 | `RAG_Platform_Master_Plan_v2.md` | §15 Phase 8 row ("BG-1 (§10.3) → feeds D3") | H-1 | Relocate model-only BG-1a to a Phase 0.5 entry |
| 3 | `RAG_Platform_Master_Plan_v2.md` | §7.1 gate 7 ("BG-1 benchmark report…") | H-1 | Reference the Phase 0.5 BG-1a report |
| 4 | `RAG_Platform_Master_Plan_v2.md` | §12 setup.ps1 bullet ("C: free space ≥ 5 GB (fail loud)") | H-2, M-1 | Add snapshot + `blocked` semantics + D:-path asserts (threshold unchanged) |
| 5 | `RAG_Platform_Master_Plan_v2.md` | §4.2 stage list ("Stages: `authn → authz_filter → …`") | H-3 | Keep canonical; add trace-recorder clarification |
| 6 | `RAG_Platform_Master_Plan_v2.md` | §5 pipeline paragraph ("cache lookup … → authorization pre-filter") | H-3 | Reorder to authz → cache; align stage counting |
| 7 | `RAG_Platform_Master_Plan_v2.md` | §10.2 / §12 (`env.ps1` descriptions) | M-1, M-5 | Add HF offline flags, log-sink path, env asserts, tooling carve-out |
| 8 | `RAG_Platform_Master_Plan_v2.md` | §12 (new "Verification commands" block) | M-2 | Concrete FTS5/Qdrant/egress/compat commands |
| 9 | `RAG_Platform_Master_Plan_v2.md` | §9.2 / §9.3 and R19 | M-3 | Stratification, blind holdout, paraphrase variants, second review |
| 10 | `RAG_Platform_Master_Plan_v2.md` | §7.1 gate 9 | M-4 | `blocked`-not-failed + documented baseline |
| 11 | `RAG_Platform_Master_Plan_v2.md` | §6 / §15 Phase 0 | 4.8 | Resolution dry-run before install; "no global installs; venv-only compat checks" |

No correction is required to `RAG_Platform_Master_Plan.md` (v1 — preserved byte-identical, SHA256 verified at audit start and end), `RAG_Platform_Strategy_Review.md`, or `RAG_Platform_Plan_Diff.md`; the H-1/H-3 contradictions are v2-internal (the diff's §8 table echoes the "M1 start" reading, so one clarifying sentence there would keep the record consistent, but the diff itself is a change record, not the normative plan).

---

## 9. Specific proposed corrections or replacement wording

**C-1 (fixes H-1) — v2 §10.3 title + §15 Phase 8 + §7.1 gate 7.**
- §10.3 title →: "Safe benchmark protocol BG-1 — model-only part **BG-1a** runs once immediately after Phase 0 (post-approval, pre-Phase-1; no benchmarks during planning); **BG-1b** (pipeline-unavailability proof) folds into Phase 6. Stop conditions (step 6) apply to both."
- §15 Phase 8 → remove "BG-1 (§10.3) → feeds D3" from the Phase 8 row; add a new row: "0.5 | **BG-1a** model benchmark (§10.3 steps 1–4, 7–8; Ollama + psutil only) with stop conditions → verdict feeds **D3** before Phase 6 | Model verdict in `docs/RESOURCE_BUDGET.md`; `blocked` outcome recorded if stop conditions trip".
- §7.1 gate 7 →: "BG-1a model benchmark report (Phase 0.5) with `ollama ps` evidence and a recorded verdict (usable-cpu / usable-gpu / too-slow / too-heavy / blocked); the §10.3 step-5 unavailability proof is covered by gate 2."

**C-2 (fixes H-2) — v2 §12 setup.ps1 bullet, threshold unchanged.**
- Replace with: "`setup.ps1` asserts: C: free space ≥ 5 GB (fail loud) **and records a C:/D: free-space snapshot in the setup report. If the C: assert fails, the observed value is recorded and the run is marked `blocked` (not `failed`) so a transient OneDrive/temp/pagefile swing can be re-run; no fallback that writes to C: is permitted.** `setup.ps1` additionally asserts `UV_CACHE_DIR`, `HF_HOME`, `TMP`, and (if R7 triggered) `UV_PYTHON_INSTALL_DIR` all resolve under `D:\rag-platform`, failing loud otherwise."

**C-3 (fixes H-3) — v2 §5 pipeline paragraph; §4.2 kept canonical.**
- §5 →: "authenticate → derive tenant from credential → **authorization pre-filter** → cache lookup (corpus/ACL-fingerprint-checked; every hit revalidated against current ACLs as defense-in-depth) → deterministic budget selection → hybrid retrieval (dense + BM25 via SQLite FTS5, reciprocal-rank fusion) → independent post-retrieval authorization verify → bounded context build → local generation with citations or pre-generation abstention (I12) → evidence ledger → response. **Cache lookup never precedes the authorization pre-filter.** Trace recording and failure attribution are **cross-cutting recorders** — every stage emits into one per-request trace (nine traced stages: authn, authz_filter, cache_lookup, retrieval, post_verify, context_build, generation, ledger, response); the recorder is not itself a pipeline stage. **Mandatory security gates:** authentication, server-derived identity (I2), authorization pre-filter, independent post-verify, abstention (I12), egress guard (I3/I4). **Advisory diagnostics (never gates):** injection quarantine flags (I8), adaptive budget policy (kill-switchable), ledger `inferred` class, failure-attribution rules, all model-based advisory tiers (I11)."

**C-4 (fixes M-1) — v2 §10.2/§12 additions.**
- "The application **file**-log sink is `D:\rag-platform\reports\logs\` (structured stderr remains the default; file logging is opt-in and always on D:). `env.ps1` also sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` so no Hugging Face hub update-check socket can open (T-ADV-09). MCP/`npx`-based development tooling uses the default npm cache on C:; that is tooling, not project artifacts, and is explicitly outside the Z9 project invariant (documented, not asserted)."

**C-5 (fixes M-2) — new §12 "Verification commands (reproducible; run at the stated milestone)" block.**
- *Phase 0 — FTS5 (R20):* inside the project venv: `python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); c.execute(\"INSERT INTO t VALUES ('hello world')\"); print(c.execute(\"SELECT * FROM t WHERE t MATCH 'hello'\").fetchall())"` → must return the row; else dense-only fallback and R20 recorded.
- *Phase 0 — Python compat (R7):* `uv run python -c "import fastapi, pydantic, httpx, pypdf, psutil, sentence_transformers, qdrant_client"` **inside the project venv** (never the system interpreter); on failure trigger R7 (3.12/3.13 via `uv python install` with `UV_PYTHON_INSTALL_DIR` on D:) and re-run the whole environment, never a partial/global install.
- *Phase 0 — uv resolution dry-run:* `uv lock --check` (or equivalent resolution-only mode) before `uv sync`, so missing 3.14 wheels surface pre-install.
- *M1 — Qdrant local persistence + filtering:* with the venv installed, create Qdrant-local at `D:\rag-platform\data\qdrant`, insert a labeled fixture, close the process, restart it, assert the same vector count and that each payload filter (tenant/document/page) returns exactly the expected ID set (gates 3/4); repeat after a document deletion to assert index consistency; assert an empty-result query returns a typed empty response, not an error.
- *M1 — BM25/FTS5 ordering:* insert documents with tied BM25 scores; assert result order is stable across repeated queries and restarts (tie-break policy: FTS5 rowid ascending); assert hybrid RRF output is deterministic for fixed inputs.
- *M1 — Egress (T-ADV-09):* run the full suite with `EGRESS_MODE=off` under a conftest-level socket guard that fails the test on any `connect()` to a non-loopback address; the guard covers the platform process only (tooling scope per C-4).
- None of these commands were executed during this audit.

**C-6 (fixes M-3) — v2 §9.3 limitation row + R19 mitigation.**
- R19 mitigation → append: "(a) report per-evidence-type stratum metrics (verbatim / paraphrase / multi-hop / unanswerable / conflict-aware); (b) author a blind holdout batch **after** thresholds are frozen, evaluated exactly once; (c) include paraphrase-supported adversarial variants (gold support present, verbatim containment absent) to measure false-'unsupported'; (d) a fixture sample receives an independent second review (machine owner) before dataset-ID freeze; (e) every report header states 'self-consistency on authored fixtures — not external validity'; (f) the later real-document set remains the only external-validity check."

**C-7 (fixes M-4) — v2 §7.1 gate 9.**
- Append: "**Measured with a documented background-app baseline recorded alongside the number. If free system RAM is already below the spike floor when the gate runs, the gate is reported `blocked` (not `failed`), BG-1 §10.3 step-6 stop conditions govern, and the budget is revised only with measured data (§10.1) — never with an assumed GPU offload.**"

**C-8 (fixes M-5) — v2 §9.3 or §12 scope sentence.**
- "The zero-egress guarantee is verified by in-process socket instrumentation (T-ADV-09) and applies to the platform runtime; configuration flags alone are not the guarantee. Development tooling (MCP servers, `npx` package resolution, package installs at Phase 0) is outside the guarantee and is documented in `SETUP_WINDOWS.md`. `env.ps1` sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` so no cache-warm embedding path opens a socket."

**C-9 (optional polish, 4.4) — v2 §9.5 report header line.**
- Add: "Deterministic containment is textual presence, not semantic support; `inferred` is an overlap proxy, not entailment; the system emits no calibrated probabilities (I13)."

**C-10 (fixes 4.8 gaps) — v2 §15 Phase 0 row.**
- Append to Phase 0 tasks: "uv resolution dry-run before install; **no global package installs — compat checks run only inside the project venv** (system Python 3.14 already contains some packages and must not mask venv gaps); single-dependency install failure ⇒ whole-environment R7 retry on D:, gate marked `blocked` meanwhile."

---

## 10. Unresolved unknowns and the evidence needed to resolve each

| # | Unknown | Evidence needed | When obtainable |
|---|---|---|---|
| U1 | LSP runtime health (all 8) in the current OpenCode run | A session that opens a `.py`/`.ts` file and observes diagnostics, or explicit LSP-startup log lines | Next coding session; not resolvable read-only here |
| U2 | Precise internal mechanism of the historical MCP timeouts (config-shape vs. timeout semantics vs. session staleness) | OpenCode source inspection or debug-level logging during a controlled reproduce (forbidden in this task) | Optional; outcome no longer operationally relevant while servers are healthy |
| U3 | FTS5 availability in the **project venv's** sqlite3 (system-interpreter measurement E20 is historical and interpreter-specific) | C-5 Phase 0 command inside the uv venv | Phase 0 |
| U4 | `qdrant-client` local-mode persistence/filter behavior on this machine | Package not installed yet (correctly); M1 C-5 tests | Phase 0 install → M1 integration |
| U5 | `qwen3:8b` alias digest identity vs. `qwen3:latest` (sizes match) | `ollama list` digest comparison at BG-1a (§10.3 step 1) | BG-1a (post-approval) |
| U6 | Model offload state, tok/s, load times, peak RAM | BG-1a with `ollama ps` evidence | BG-1a |
| U7 | C: free-space volatility driver (OneDrive vs. pagefile vs. temp) | Repeated snapshots; process-level attribution | Noted, not blocking (C-2 contains it) |
| U8 | Whether sentence-transformers/transformers perform network calls with warm cache on this exact version set | T-ADV-09 suite run with C-4's offline flags absent vs. present | M1 egress suite |
| U9 | GPU-torch availability on Python 3.14 vs. fallback-version need (R7) | Resolution dry-run + venv import test (C-5) | Phase 0 |
| U10 | Plugin load failures' impact on intended OpenCode behavior | Dedicated OpenCode diagnostic session (config changes forbidden here) | Optional, out of project scope |

---

## 11. Recommended corrected phase sequence

```
Phase 0    Scaffold: git init (D8) + .gitignore; uv venv + pinned lock on D:; license + pip-audit;
           FTS5 detection (C-5); uv resolution dry-run + venv-only compat check (C-5/C-10);
           setup.ps1: C: ≥5 GB assert with snapshot + blocked-semantics, D:-path env asserts (C-2/C-4)
Phase 0.5  BG-1a — model-only benchmark (§10.3 steps 1–4, 7–8) with stop conditions; verdict feeds D3;
           blocked outcome recorded if stop conditions trip (C-1)
Phase 1    Config: egress off, kill switches, startup-rejection tests, redacted logging, /health (I3/I4/I6)
Phase 2    Storage: SQLite schema + FTS5 + trace tables; Qdrant-local adapter; persistence/filter/
           deletion-consistency integration tests (C-5)
Phase 3    Embeddings wrapper; MiniLM cache copy C:→D: (D2); HF offline flags active (C-4)
Phase 4    Ingestion: loaders, chunker, injection scan, declared-date capture
Phase 5    Hybrid retrieval: dense + FTS5 + RRF; BM25 ordering-stability test; dense-only fallback (C-5)
Phase 6    Generation: Ollama client, bounded context, citations, abstention; BG-1b unavailability proof
           (Z11/gate 2); egress suite with socket instrumentation (T-ADV-09, C-8)
Phase 7    API + CLI (report skeleton)
Phase 8    Frozen corpus with all planted structures (per C-6 stratification) + eval harness + trace recorder
           → D11 provisional margins prepared for re-freeze → Gate M1
M2         §7.2 (unchanged order) → Gate M2
M3         §7.3 (ledger → attribution → fixtures scoring → advisory tiers last) → Gate M3
M4         §7.4 (budget A/B → cache → concurrency → conditional graph if D10) → Gate M4 → final report
```

Prerequisites and stop conditions are as defined in v2 §10.3 (BG-1a: RAM < 2 GB at any sample, swap activity, user-reported sluggishness, >15 min runtime → abort, unload, record) and in C-2 (`blocked`, never a C:-writing fallback).

---

## 12. Final readiness verdict

# `READY AFTER CORRECTIONS`

**Explanation.** The four planning documents are consistent in their constraints (Z1–Z12), security invariants (I1–I13), zero-cost discipline, and D:-only intent; no Critical finding exists; the MCP tooling layer is healthy and the claimed repairs (universal filesystem roots, repository-independent Git, eight LSPs) are verified present in the current configuration rather than assumed. Three High findings must be corrected *in the documents* before Phase 0 to avoid rework and a security-ordering ambiguity:

**Blockers to clear before Phase 0 (all planning-wording fixes, no code required):**
1. **H-1** — BG-1 sequencing contradiction (C-1).
2. **H-2** — C:-guard volatility handling, threshold unchanged (C-2).
3. **H-3** — canonical pipeline ordering (C-3).

Five Medium items (M-1 … M-5, fixes C-4/C-5/C-6/C-7/C-8/C-10) should be applied in the same pass so the gates are reproducible, self-bias is visible, and the egress guarantee is honest and passable. No document has been corrected by this audit — this report is advisory only.

---

## Final validation (read-only confirmation)

- **Source documents untouched:** SHA256 recomputed at audit end — all four planning documents byte-identical to their start-of-audit values (v1 additionally matches the SHA256 recorded in `RAG_Platform_Plan_Diff.md` §1 and the strategy review §1).
- **No implementation or system changes:** no application code, dependencies, models, services, language servers, or MCP servers were created, installed, started, stopped, or restarted; Git state unchanged (no repository exists — verified); no OpenCode/MCP/LSP configuration, environment variables, registry entries, or system settings were modified.
- **No files written to C:.** All commands were read-only (listings, hashes, log searches, PATH lookups, env presence checks, version queries, one local read-only Ollama inventory request, one GPU query).
- **Only new artifact:** `D:\rag-platform\RAG_Platform_Preflight_Audit_20261018-1654.md` (this file). The pre-existing `RAG_Platform_Preflight_Audit.md` was preserved untouched. All required sections (1–12) are present in this report.

*End of preflight audit — 2026-10-18 ~16:55 +05:30.*
