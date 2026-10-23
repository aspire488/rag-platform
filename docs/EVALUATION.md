# Evaluation harness (Phase 8) — plan v2.4 §9.1–§9.6, §15 row 8

**Added 2026-10-21.** This document describes the frozen-corpus evaluation
workflow exactly as implemented and verified. The canonical requirements live in
`RAG_Platform_Master_Plan_v2.4.md`; this file documents behavior, not new rules.
Every number below is a measured value from a recorded run or a fresh test run
of 2026-10-21; nothing is inferred, rounded up, or borrowed from another
configuration (Z8).

---

## 1. Safety envelope (what an eval run is allowed to do)

- **Generation is disabled for every run.** `local_generation_enabled=False`,
  `generation_model=None`, `retrieval_score_floor=None` — the shipped defaults
  are the only configuration the harness accepts. If settings ever carry
  `local_generation_enabled=True`, `run_eval` raises `EvalConfigError` and the
  CLI exits `2`; no question is asked, no file is ingested, no report is
  written. There is deliberately **no CLI flag that enables generation** (the
  argparse destination set is exactly `dataset, split, tuning, top_k, out`).
- **Offline by construction.** An eval run performs zero socket operations of
  any kind — proven under a total socket guard (`connect()` *and*
  `getaddrinfo()`, loopback included) in
  `tests/egress/test_eval_no_outbound.py`, covering the harness run, the CLI
  command, a tampered-corpus refusal, and two back-to-back runs.
- **No model loads.** A run never imports `sentence_transformers` or `torch`
  (the lazy MiniLM loader is never triggered).
- **Read-only corpus.** The frozen dataset is only read; a full byte-snapshot
  (SHA-256 per file, before/after) proves verification changes nothing
  (`tests/eval/test_frozen_corpus.py`).
- **Isolated per-run storage.** Ingestion and evaluation happen in a fresh
  SQLite database per run under `data\eval_runs\<run_id>\` (D:, gitignored).
  The application database is never touched by `eval` (only the explicit,
  manifest-confined `ingest` subcommand writes to a database of its choosing).

## 2. Frozen dataset contract

Layout: `corpora\<dataset_id>\` with `manifest.json`, `documents\…`,
`questions\questions_dev.jsonl`, `questions\questions_test.jsonl`.

**Manifest** (`schema = rag-platform-eval-manifest/1`) — verified fully before
anything else happens; any failure aborts the run (`DatasetIntegrityError`,
CLI exit `3`):

- required keys, `dataset_id` (must match `\A[a-z0-9][a-z0-9_-]*\Z`, at most 32
  characters — never normalized/stripped; enforced during verification), chunk
  parameters (`chunk_size=1000`,
  `chunk_overlap=150` — must equal the chunker's configured values);
- exactly the two splits present (`dev`, `test`), each question file with its
  SHA-256 and size;
- per-file SHA-256 + size for every listed document;
- **path confinement**: absolute paths, drive letters, backslashes, `..`
  traversal, leading dots, symlinks, and duplicate paths are rejected;
- documents may not carry a split; question files must declare one.

**Question records** (JSONL) — keys `id`, `type`, `expected_outcome`,
`question`, `gold_chunk_ids`, `gold_answer`, `gold_answer_span`, `structures`:

- `type` ∈ {single-hop, multi-hop, unanswerable, conflict-aware,
  injection-adjacent}; `expected_outcome` ∈ {answer, abstain, flag-conflict};
- label rules: `answer` ⇒ non-empty gold set + gold answer; `abstain` and
  `unanswerable` ⇒ no gold; `gold_answer_span` must reference a gold chunk with
  `0 ≤ char_start < char_end`; IDs unique per file; ≤ 2000 chars per question;
  ≤ 500 questions per file.

**The frozen corpus (`corpora\synthetic_v1`, locally authored):** 24 documents
— 10 TXT / 8 MD / 6 PDF, 63,090–84,641 bytes each (within the plan's
50–150 KB band) — plus 114 questions: **dev 70** (27 single-hop, 24 multi-hop,
9 unanswerable, 3 conflict-aware, 7 injection-adjacent) and **test 44** (13/16/
7/3/5), i.e. 40 multi-hop and 16 unanswerable overall. Gold spans and gold
chunk IDs were computed with the production chunker (`load_document` /
`chunk_text` / `document_id` / `chunk_id`), not by hand.

> **Storage status (owner decision pending).** `.gitignore` line 20 states
> "frozen eval datasets ARE committed; raw corpora are not", yet line 21
> ignores `corpora/` entirely — the frozen corpus therefore cannot be staged
> as-is. Per constraints, `.gitignore` was **not** changed and the corpus is
> **not** staged. The precise conflict and the minimal proposed exception are
> recorded in `RAG_Platform_Plan_Change_Note_v2.4.md` §12 for the owner.

## 3. Splits and tuning protection

- Thresholds tune on **dev** only; **test** is evaluated once per candidate
  configuration.
- `load_questions(..., tuning=True)` refuses the test split **before the file
  is opened** — proven by deleting the test file: the tuning refusal message
  differs from the ordinary "unreadable" error, which demonstrates the
  ordering.
- Every run records `prior_test_split_evaluations` — a dataset-wide count of
  prior test-split `run.json` files — in its report (surfacing
  overfitting-by-repetition). **Limitation:** the counter reflects what is on
  disk; deleting `data\eval_runs\` resets it.
- No test-split evaluation has been burned: the recorded runs are dev-only
  (`prior_test_split_evaluations = 0`).
- No external grading, downloaded judge, or comparison against other systems
  exists anywhere (author–evaluator risk disclosed per R19; see §9).

## 4. Running an evaluation (CLI)

```powershell
$env:PYTHONPATH = "src"        # once per session (README §7)
.venv\Scripts\python.exe -m rag_platform.cli eval --dataset corpora\synthetic_v1 --split dev
.venv\Scripts\python.exe -m rag_platform.cli eval --dataset corpora\synthetic_v1 --split dev --tuning
.venv\Scripts\python.exe -m rag_platform.cli ingest --dataset corpora\synthetic_v1
```

- `eval` options: `--split {dev,test}` (default `dev`), `--tuning`,
  `--top-k` (1–20, default 10), `--out` (default `<project>\reports`).
  Both `--dataset` and `--out` must resolve inside the project root.
- `ingest` ingests **only** manifest-listed documents, after full manifest
  verification, into a SQLite database; ambient files in the same folders are
  ignored (tested). Question labels are ignored by ingest — it never reads
  gold into the index.
- **Exit codes:** `0` clean run; `2` invalid input (bad top-k, missing/outside
  dataset, `--out` outside the project, `--tuning` with `--split test`,
  `EvalConfigError`); `3` integrity failure (tampered manifest/file, gold
  mismatch) or an unexpected internal error (exception *class name* only —
  never a traceback or message); `4` the run completed but some questions
  raised typed errors — per-query errors stay in the denominators, the report
  is written with `error_classes` and `retrieval_error_count`.
- Reports print a JSON summary to stdout (report paths included); raw queries
  are **not** dumped by default. The advisory quarantine notices you may see
  (`quarantined_chunks=1`) are metadata-only findings on planted injection
  documents — content is preserved, chunks excluded from use.

## 5. Metrics (stdlib-only; definitions frozen in `eval/metrics.py`)

| Metric | Definition | Denominator / honesty rule |
|---|---|---|
| Recall@5 / @10 | \|gold ∩ top-k\| / \|gold\| | macro over answer-labeled questions with a non-empty gold set; a retrieval error scores 0 for that question — **errors never leave the denominator** |
| Precision@5 / @10 | \|gold ∩ top-k\| / k | missing slots count non-relevant (denominator is k) |
| MRR | 1 / rank of first gold hit (0 if none) | same answerable set; errors → 0 |
| nDCG@10 | binary gains: Σ 1/log₂(rank+1) over gold in top 10, normalized against the ideal ordering of min(\|gold\|, 10) | same answerable set; errors → 0 |
| Abstention correctness | evidence-level abstentions (`no_candidates`, `insufficient_evidence`, `no_usable_evidence`) among **abstain-labeled** questions / abstain-labeled count | `generation_unavailable` counts **incorrect** (disabled generation is not a correct refusal); the count is printed in the metric note |
| False-refusal rate | answer-labeled questions that received an evidence-level abstention / answer-labeled count | flag-conflict questions are a separate class — no conflict metric at M1, none invented |
| Retrieval-only rate | responses with `generation_outcome == "unavailable"` / all | reflects configuration, not quality |
| Citation validity | citations whose span resolves inside a served chunk / total citations | only when citations exist; otherwise `not_measured` |
| Token-F1 | lexical overlap, predicted vs gold answer | only over pairs where **both** an actual answer and a gold answer exist; with generation disabled there are none → `not_measured` (never 0) |
| Latency p50/p95 | nearest-rank percentiles of per-query wall time | volatile; single machine; no cross-machine claim |
| Stage timings | p50/p95 of recorded `trace_stages` timings per stage | volatile; only stages a query actually reached are counted |

**Status vocabulary.** Metric rows carry `measured` or `not_measured`
(`value=None` when unmeasured — never zero-filled, never 100%). Plan §7.1/§9.1
gate-level statuses (`passed/failed/skipped/blocked/not-measured`) are used only
in the Gate M1 checklist, never to relabel a metric: **M1 defines no numeric
thresholds, so a measured value is evidence, not a pass** — that limitation is
printed in every report header (§9.4).

## 6. Reports

- Files: `reports\eval_<dataset>_<split>_<UTCstamp>_<runhex>.json` and `.md`
  (gitignored). Existing reports are never overwritten — same-second runs get
  distinct run-hex suffixes; re-writing the same result yields byte-identical
  output.
- Structure (`schema: rag-platform-eval-report/1`): a **deterministic** section
  (dataset/split/top-k config labels, manifest/question-file/lockfile SHA-256,
  `metric_counts`, all non-latency metric rows, outcome/abstention/served-by
  distributions, `error_classes`, `ingest_summary`, and ID-only per-question
  records) separated from a **volatile** section (run id, timestamps, wall
  time, latency rows, stage timings, `prior_test_split_evaluations`,
  environment). Two runs over the same inputs produce identical
  `deterministic` bytes.
- The Markdown renderer prints the §9.4 known-metric limitations in every
  header, plus the run's outcomes, distributions, stage-timing table, and
  per-question rows.
- No raw question text, answer text, passage text, absolute paths, or exception
  messages appear in either format (tested).

## 7. Trace linkage

- Deterministic request IDs: `eval:<dataset>:<split>:<question_id>` — every
  report row links to its `traces`/`trace_stages` rows in the run database.
- Stage vocabulary (frozen in `observability.py`): retrieval ∈ {ok,
  no_candidates, insufficient_evidence}; context_build ∈ {ok, budget_invalid,
  budget_exhausted}. A query that abstains before context building honestly has
  no `context_build` stage.
- The harness attaches an **observer** to the shared `run_query` — the only
  addition it makes to the pipeline. With the default `observer=None` the trace
  keeps its exact Phase 7 shape (three stages, retrieval timing NULL); with an
  observer, the measured retrieval timing and the `context_build` row reach
  both the callback and the database (regression-tested in
  `tests/test_observer_trace.py`, including a raising observer, which is
  contained without affecting the response or the trace).
- Content rules unchanged: digests, enums, counts, timings only; `excerpt`
  always NULL; `trace_authz` empty; no queries/prompts/passages/paths/keys.

## 8. Recorded dev run (2026-10-21)

`reports\eval_synthetic_v1_dev_20261021T075202Z_dc9ab460.{json,md}` — 70 dev
questions, 0 errors, exit 0:

| | | | |
|---|---|---|---|
| recall@5 **0.8934** | recall@10 **0.9754** | precision@5 **0.2492** | precision@10 **0.1393** |
| MRR **0.9145** | nDCG@10 **0.8938** | abstention correctness **0.0** | false-refusal **0.0** |
| retrieval-only **1.0** | latency p50 **11.23 ms** | latency p95 **15.28 ms** | token-F1 / citation validity **not_measured** |

- Counts: 61 answerable / 9 unanswerable / 70 generation-unavailable /
  0 retrieval errors; retrieval outcomes `{ok: 70}`; abstention reasons
  `{generation_unavailable: 70}`; `served_by {retrieval_only: 70}`.
- Stage timings recorded for all four stages (70 samples each).
- **Determinism:** these values are byte-identical to the earlier independent
  dev run of the same day (`…T062124Z`), across separate harness invocations.
- `prior_test_split_evaluations = 0` — the test split has never been run.

## 9. Findings from the first runs (honest, recorded)

1. **`no_candidates` is unreachable for grammatical questions with the default
   retriever.** The lexical retriever OR-joins unfiltered query tokens, so any
   question that shares a token with the corpus returns candidates —
   evidenced by `{ok: 70}` on the real corpus. The fixture dataset still
   exercises the path (a gibberish question yields `no_candidates`), so the
   abstention plumbing is tested end to end.
2. **Abstention correctness on the real dev split is 0.0 (measured).** With
   the score floor unset (`retrieval_score_floor=None`) and generation
   disabled, every abstain-labeled question answered `generation_unavailable`,
   which is *not* a correct abstention. This is exactly what the frozen
   specification predicts: correct evidence-level refusal requires an approved
   floor/D3 verdict (both gated, untouched). The plan's ~90 % abstention target
   belongs to **M3 (D11)** — it is not an M1 criterion, and Gate M1 criterion 6
   (which only requires producing the metrics with a status column) is
   unaffected.
3. **Fixture coverage proves both refusal paths.** The test fixture dataset
   (2 documents, 4 questions) shows `no_candidates` (gibberish) and
   `generation_unavailable` (in-domain) abstentions with abstention
   correctness 0.5 (1/2) — spec-honest and covered by tests.

## 10. Known limitations (printed in every report header)

- Recall/precision/MRR/nDCG measure retrieval overlap with gold chunk IDs
  only; they do not measure answer correctness.
- Token-F1 is lexical overlap with gold answer spans; `not_measured` until a
  generation-enabled run is authorized.
- Citation validity counts citations whose span resolves inside a served chunk;
  it does not verify that the cited span supports the claim.
- Abstention correctness is judged against authored expectations in the frozen
  corpus; it is not a human preference judgment.
- Latency p50/p95 are nearest-rank percentiles from a single machine; no
  cross-machine generalization is claimed.
- M1 has no frozen numeric thresholds (§7.1): a measured value is evidence,
  not a pass — threshold decisions are the owner's.
- Same-author corpus and gold labels (R19): mitigations are the frozen hashed
  splits, published item lists, and the ban on external-comparison claims.

## 11. Gate M1

Evidence checklist: `reports\gate_m1_evidence_checklist_20261021.md` —
criterion 6 (eval harness end-to-end) is **passed**; criteria 1, 5, 7 and the
gate-9 spike are **blocked** with recorded reasons (generation-enabled run /
BG-1a verdict / score-floor approval / memory-spike measurement all pending
owner decisions). No gate is claimed fully passed while blocked items remain
(§9.1 status semantics).
