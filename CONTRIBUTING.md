# Contributing

Thanks for helping improve rag-platform. Changes should preserve the project's local-first, resource-bounded, evidence-honest design.

## Before changing code

1. Read [README.md](README.md) for the current implementation boundary.
2. Read [the product direction](docs/PRODUCT_DIRECTION.md) for the target experience.
3. Consult [the canonical plan](RAG_Platform_Master_Plan_v2.4.md) before changing a gated or owner-approved decision.
4. Read [the resource budget](docs/RESOURCE_BUDGET.md) and [evaluation contract](docs/EVALUATION.md) before changing model, storage, retrieval, or evaluation behavior.

Do not treat older plans, audits, or change notes as current implementation claims. They are retained as historical decision evidence.

## Development setup (Windows / PowerShell)

The repository currently documents a D: project layout. Use the project script before installing dependencies so caches and temporary files remain in the configured project location.

```powershell
. .\scripts\env.ps1
uv sync --group dev
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m pytest
```

Run the focused test while iterating, then the complete suite before submitting. Do not claim a test passed unless you ran it and captured the result.

## Change requirements

- Keep changes narrow and typed. Add regression tests for bug fixes.
- Preserve fail-closed validation, path containment, redacted logging, request bounds, and zero-egress defaults.
- Never add credentials, real user documents, local databases, generated reports, caches, or model weights to Git.
- Never silently enable generation, external providers, billing, or network access.
- New UI must consume real API contracts; do not ship mock metrics or pretend status.
- Update documentation and examples whenever behavior or commands change.
- Mark unavailable measurements as `not-measured`; never turn missing data into zero or a pass.
- Avoid breaking schema/API changes without migrations and a compatibility note.

## Pull requests

Include:
- Problem statement and concrete user impact.
- Implementation summary and why the change belongs at this layer.
- Regression tests added.
- Exact commands executed and their outcomes.
- Security, privacy, resource, and compatibility implications.
- Explicitly state what was not tested.

Keep screenshots for UI changes. Keep performance claims tied to reproducible benchmark reports, including hardware and configuration.

## Review checklist

- [ ] The change addresses a real bug or user workflow.
- [ ] Tests cover the regression or new behavior.
- [ ] Full test suite run, or the blocker is stated plainly.
- [ ] No secrets, private corpora, reports, or machine-specific paths are committed.
- [ ] Offline/local-first invariants remain intact.
- [ ] Documentation reflects the actual code.
- [ ] No unmeasured performance or quality claim is introduced.
