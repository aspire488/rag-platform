<h1 align="center">
  <img src="docs/assets/retrieval-lab-hero.svg" width="720" alt="rag-platform">
</h1>

**rag-platform** is a local-first retrieval-augmented generation engine for one Windows laptop with 15 GB of RAM.
Its data, caches, virtual environment and databases stay inside the project directory, and it sends nothing over the
network by default. It is built in order, from the storage layer up, against a written plan, and it reports what it
has not done as plainly as what it has. The header lists the nine stages every request will pass through, from
`authn` to `response`. None of them runs yet.

> [!NOTE]
> **Where it stands:** phases 0 to 3 of 8 are committed, and all 106 tests passed on 2026-10-20. **There is no
> working RAG pipeline yet:** nothing ingests a document, answers a query or generates text.

[Architecture](#architecture) | [Status](#status-board) | [Inside the code](#inside-the-code) |
[Run it](#run-it) | [Limits](#known-limits) | [Plan v2.4](RAG_Platform_Master_Plan_v2.4.md) |
[Resource budget](docs/RESOURCE_BUDGET.md)

## Architecture

<p align="center">
  <img src="docs/assets/architecture.svg" width="720" alt="Architecture as committed. Runs today: Settings feeds create_app, which serves GET /health and installs redacted logging. Built and tested on their own but not wired together: MiniLMEmbedder (384-dim, lazy, CPU), QdrantLocalStore (file-backed, no URL) and SQLite with FTS5 (WAL, trace tables); their wires end in open terminals. Planned path into the stores: documents (pdf, txt, md), then ingest (load, chunk, scan; in progress, not committed), then index (embed and store into the three blocks). After that, queries take the nine trace stages from the header.">
</p>

The picture has three zones, and the gaps between them are the honest part:

1. **Runs today.** `create_app()` validates `Settings`, installs the redacting log handler and serves one route,
   `GET /health`. This is the only code path that runs end to end.
2. **Built, not wired.** The embedder, the vector store and the SQLite schema are real modules with their own
   tests. No code calls one from another yet.
3. **Planned.** Ingestion (phase 4, in progress) will feed the three stores. Queries then take the nine stages in
   the header. Everything past the stores is design from the plan (§5 and §15), not code yet.

## Status board

| Mark | Meaning |
| --- | --- |
| ● | Built and wired into the running app |
| ◐ | Built and tested on its own; nothing calls it yet |
| ✎ | In progress, not committed |
| ○ | Planned in the canonical plan; no code |
| ⊘ | Off by default |
| – | Not measured |

| Component | Status | Tests and notes |
| --- | --- | --- |
| Settings and startup rejection, [`config.py`](src/rag_platform/config.py) | ● built, wired | `test_config.py` |
| Redacted logging, [`logging_setup.py`](src/rag_platform/logging_setup.py) | ● built, wired | `test_logging_redaction.py` |
| `GET /health`, the only route, [`main.py`](src/rag_platform/main.py) | ● built, wired | `test_health.py`, `egress/` |
| SQLite (WAL) + FTS5 + trace tables, [`db/`](src/rag_platform/db) | ◐ built, not wired | `test_db_sqlite.py` |
| Qdrant local adapter, [`vectorstore/`](src/rag_platform/vectorstore) | ◐ built, not wired | `test_vectorstore_qdrant.py` |
| MiniLM wrapper, [`minilm.py`](src/rag_platform/embeddings/minilm.py) | ◐ built, not wired | `test_embeddings_wrapper.py`; stub model only, the real model has never loaded |
| Model cache copy with SHA-256 check, [`cache.py`](src/rag_platform/embeddings/cache.py) | ◐ built, not wired | `test_embeddings_cache.py` |
| Ingestion: loaders, chunker, injection scan (phase 4) | ✎ in progress | local work, not on `main` yet |
| Hybrid retrieval: dense + FTS5 + RRF (phase 5) | ○ planned | |
| Generation, citations, abstention (phase 6) | ○ planned | local Ollama adapter only at M1 |
| `LOCAL_GENERATION_ENABLED` | ⊘ off by default | no generation code exists for it to switch on yet |
| `EGRESS_MODE` | ⊘ off by default | `egress/test_no_outbound.py` |
| API + CLI (phase 7); eval harness and trace recorder (phase 8) | ○ planned | |
| Cloud generation providers (phases 9 to 11) | ○ proposed | decisions D13 to D18 pending |
| Model benchmark BG-1a (phase 0.5), [`bench_model.py`](scripts/bench_model.py) | – not measured | attempts stopped at the resource floors; see [`RESOURCE_BUDGET.md`](docs/RESOURCE_BUDGET.md) |

Note the difference between the two "off" rows. Egress is a working guard: invalid or half-configured egress settings
stop the app before it starts. Local generation is a flag with nothing behind it yet.

## Inside the code

### The one live surface

`create_app()` builds the FastAPI app only from valid settings. OpenAPI and the docs pages are switched off, and
`/health` reports labels and booleans, never URLs, paths or keys. This is the actual response from the committed code
with default settings:

```json
{
  "status": "ok",
  "service": "rag-platform",
  "version": "0.0.1",
  "egress_mode": "off",
  "egress_allowlist_configured": false,
  "local_generation_enabled": false,
  "paths_on_project": true
}
```

### Settings that refuse to start

[`Settings`](src/rag_platform/config.py) is a `pydantic-settings` model with `extra="forbid"`. It reads the
environment and an optional project-root `.env`.

| Key | Default | What is enforced |
| --- | --- | --- |
| `EGRESS_MODE` | `off` | `off` or `allowlist`; `off` with allowlist entries is rejected, and so is `allowlist` without any |
| `EGRESS_ALLOWLIST` | `[]` | JSON list of exact entries, validated one by one |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | loopback host only, plain origin, no embedded credentials |
| `PORT` | `8000` | valid port range |
| `LOCAL_GENERATION_ENABLED` | `false` | boolean |
| `ADAPTIVE_RETRIEVAL` | `off` | `on` or `off` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `DATA_DIR`, `REPORTS_DIR` | `data`, `reports` | must resolve inside the project root |

### Storage, laid down before the pipeline

- **SQLite** ([`db/sqlite.py`](src/rag_platform/db/sqlite.py)): every connection runs in WAL mode with foreign keys
  enforced, and paths that escape the project root are refused. Migrations are versioned through `user_version`.
- **The schema** ([`db/schema.sql`](src/rag_platform/db/schema.sql)) holds an FTS5 keyword index (`chunk_fts`) and
  three trace tables. `trace_stages` only accepts the nine stage names from the header image, one row per stage per
  request, with excerpts capped at 200 characters. `trace_authz` records allowed and denied document IDs, never
  content. Nothing writes to these tables yet; the recorder arrives in phase 8.
- **Qdrant** ([`vectorstore/qdrant_local.py`](src/rag_platform/vectorstore/qdrant_local.py)): `QdrantLocalStore` takes
  a filesystem path and has no URL or API-key parameter, so a remote connection cannot be configured. Inputs are
  validated before any write; reads on a missing collection return empty, and writes to a missing collection fail.
- **Two stores, no transaction across them.** [`test_storage_consistency.py`](tests/test_storage_consistency.py)
  records that boundary on purpose: removing a vector leaves SQLite rows alone until phase 4 defines a combined
  contract.

### Embeddings that load nothing until asked

`MiniLMEmbedder` wraps `sentence-transformers/all-MiniLM-L6-v2`. Importing or constructing it imports neither
`sentence_transformers` nor `torch`. The model loads on the first `encode()` call, with `local_files_only=True` and from
a cache inside the project. The 384-dimension contract is a constant taken from the cached model's config files, and
every `encode()` with the default model is checked against it. The tests use a stub model; **the real model has not been loaded on this machine yet.**

## Design decisions

| Decision | Why | Where |
| --- | --- | --- |
| Local core, egress off by default | Ingestion, retrieval and storage must work with every LLM provider unavailable | [`config.py`](src/rag_platform/config.py), plan §2 (Z2, Z11) and §5.4 |
| Everything under the project root | One directory to back up, audit and delete; Ollama's models stay in Ollama's own store | `resolve_under_project`, [`scripts/env.ps1`](scripts/env.ps1) |
| Hard resource floors over features | A 15 GB laptop must stay usable: 3 GB start gate, 2 GB emergency floor | [`docs/RESOURCE_BUDGET.md`](docs/RESOURCE_BUDGET.md) |
| Typed failures, honest statuses | Every result is `passed`, `failed`, `blocked`, `skipped` or `not-measured`, and none is quietly downgraded | plan §9.1, Appendix B |
| CPU-only torch, no provider SDKs | Smaller install, no hidden network clients | [`pyproject.toml`](pyproject.toml) |

## Run it

Requirements: Windows, a `D:` drive, [uv](https://docs.astral.sh/uv/) and PowerShell. The scripts assume the project
lives at `D:\rag-platform`. Run everything from the project root.

```powershell
. .\scripts\env.ps1          # process-only env: caches and temp under D:\rag-platform, HF offline, EGRESS_MODE defaults to off
uv sync                      # pinned dependencies, CPU-only torch index
.\scripts\setup.ps1          # environment checks, writes a dated report under reports\
.venv\Scripts\python.exe -m pytest
```

To serve `/health` locally (tests do not start a server):

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m uvicorn --factory rag_platform.main:create_app
```

Last run, 2026-10-20: **106 passed** from a clone on `D:`. From any other drive, the two tests that assert the `D:`
layout fail by design and the other 104 pass. Tests are hermetic: no network, no model load, fixtures under
`data\tmp`. A `.env` file is optional; copy
[`.env.example`](.env.example), which keeps egress off.

<details>
<summary>Stack and repository layout</summary>
<br>

| Layer | Choice |
| --- | --- |
| Runtime | Python 3.14 (`requires-python = ">=3.14"`), managed with uv |
| API | FastAPI + uvicorn |
| Config | pydantic, pydantic-settings |
| Embeddings | sentence-transformers on CPU-only torch |
| Vector store | qdrant-client in local, file-backed mode |
| Keyword store and traces | SQLite (stdlib) with FTS5 |
| Documents (phase 4) | pypdf |
| Resource gates | psutil |
| Tests and audit | pytest, pytest-cov, pip-audit |

Not added on purpose: provider SDKs, LiteLLM-style gateways, agent frameworks, hosted observability, and `networkx`
(deferred to a conditional M4 experiment). `.env`, `data/`, `reports/` and `.cache/` (including the model copy) are
gitignored.

```text
src/rag_platform/
  config.py            validated settings, path containment
  logging_setup.py     redacting log handler
  main.py              FastAPI app factory, /health only
  db/                  SQLite opener, migrations, schema.sql
  vectorstore/         Qdrant local adapter
  embeddings/          lazy MiniLM wrapper, model cache copy and verify
tests/                 hermetic tests; egress/ holds the no-outbound check
scripts/               env.ps1, setup.ps1, check_fts5.py, bench_model.*
docs/                  RESOURCE_BUDGET.md, README assets
data/ reports/ .cache/ gitignored runtime state
```

</details>

## Known limits

- **No pipeline.** No ingestion, retrieval, generation, CLI or evaluation is on `main`.
- **No real-model run.** The embedding wrapper has only been exercised with a stub.
- **No benchmark numbers.** BG-1a has no valid result, and this repository claims no throughput, latency or quality
  figures.
- **The 90 % RAM ceiling is a policy, not code.** The app can only refuse or stop its own work (plan §10.4).
- **Windows and `D:` only.** Two tests assert the project lives on `D:` and fail anywhere else.
- **Cloud providers** are a proposal in plan v2.4. None is integrated, verified or enabled.

## What comes next

Committed so far, one commit per phase: [`282cd11`](https://github.com/aspire488/rag-platform/commit/282cd11) scaffold and checks,
[`7cee270`](https://github.com/aspire488/rag-platform/commit/7cee270) config, logging and `/health`, [`c27af5e`](https://github.com/aspire488/rag-platform/commit/c27af5e) SQLite and
Qdrant, [`9acb0d3`](https://github.com/aspire488/rag-platform/commit/9acb0d3) MiniLM wrapper and cache.

| Phase | Builds | Unlocks |
| --- | --- | --- |
| 4 | Loaders, chunker, injection scan, declared dates | The first rows in `chunk_fts` and Qdrant |
| 5 | Dense + FTS5 retrieval fused with RRF, post-verify stub, score floor | Wires the three blocks together |
| 6 | Provider-neutral generation, bounded context, citations, abstention | Retrieval-only answers by default |
| 7 | API and CLI | A usable surface beyond `/health` |
| 8 | Frozen corpus, eval harness, trace recorder | Gate M1, and the first rows in `trace_stages` |

Milestones M2 to M4 (tenancy, evidence ledger, adaptive budget) follow; see plan §7. Start reading at
[`main.py`](src/rag_platform/main.py) for the live path, [`schema.sql`](src/rag_platform/db/schema.sql) for the
shape of what is coming, and [the plan](RAG_Platform_Master_Plan_v2.4.md) for why.
