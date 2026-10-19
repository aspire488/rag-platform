# RAG Platform

A small, local-first retrieval-augmented generation **engine** with an Evidence Ledger,
a deterministic Failure Observatory, and a frozen offline evaluation harness.
Full specification: `RAG_Platform_Master_Plan_v2.3.md`.

## Status (honest)

- **Phase 0 complete** (scaffold, local Git repo, pinned dependencies, environment checks).
- **Not implemented yet.** No application code exists; nothing is runnable end-to-end.
- **Model decision pending:** BG-1a benchmark (Phase 0.5) has not been run; the generation
  model's usability is unmeasured.
- The owner approved implementation start (D1) on 2026-10-19, Phase 0 only.

## Quickstart (Phase 0 only)

```powershell
# from D:\rag-platform
. .\scripts\env.ps1     # project-local environment (no global changes; all writes on D:)
.\scripts\setup.ps1     # destination-aware checks + dated report under reports\
uv sync                  # install pinned dependencies into .venv on D:
```

`docs/SETUP_WINDOWS.md` (full quickstart) arrives with Phase 7.

## Constraints in force

Local-first and zero-cost: no paid services, no cloud dependencies, no Docker, no API keys,
outbound network disabled by default (`EGRESS_MODE=off`), all project data on `D:`.
Existing C: tools (Ollama + models) are read/executed in place, never relocated.
