-- Phase 2 baseline schema - migration v1 (plan v2.4 sections 15 Phase 2,
-- 6 storage choices, 4.2 trace design; D5: SQLite WAL + FTS5 approved).
--
-- Scope discipline: ONLY what the plan assigns to Phase 2 - the FTS5 keyword
-- index structure and the trace tables. Document/chunk metadata, tenancy/ACL,
-- audit, and cache tables belong to Phases 4 and M2/M4 and are deliberately
-- NOT created here; they arrive as future versioned migrations.

-- Keyword (BM25) index target - plan section 6 "Keyword store: SQLite FTS5".
-- Deliberately a standalone (non-external-content) FTS5 table: the chunk
-- metadata table arrives with Phase 4 ingestion as its own migration, and a
-- standalone index can be populated/rebuilt then without a content-table
-- dependency. Keys are stored UNINDEXED so RRF fusion (Phase 5) can join on
-- chunk_id/doc_id without tokenizing identifiers.
CREATE VIRTUAL TABLE chunk_fts USING fts5(
    content,
    chunk_id UNINDEXED,
    doc_id UNINDEXED,
    tokenize = 'unicode61'
);

-- One row per /query request - plan section 4.2 design point 1:
-- request ID, timestamp, tenant (ID only), served_by, degradation reason,
-- cache status. No content, no secrets (I6); tenant_id is populated once
-- M2 server-derived identity exists (NULL before that - auth does not
-- exist yet in this phase).
CREATE TABLE traces (
    id INTEGER PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    tenant_id TEXT,
    served_by TEXT,
    degradation_reason TEXT,
    cache_status TEXT
);

-- One row per pipeline stage per trace - plan sections 4.2/5: the nine
-- normative stage names (CHECK-enforced), start/end timing, outcome, bounded
-- input/output digests, and stage scores. 'outcome' has no CHECK here: the
-- Phase 8 trace recorder defines its enum; inventing values now would
-- pre-empt the recorder. excerpt is capped at the I7/section 10.1 hard cap
-- of 200 chars at the storage layer. UNIQUE(trace_id, stage) makes the
-- plan's "one nine-stage trace per request" structurally enforceable.
CREATE TABLE trace_stages (
    id INTEGER PRIMARY KEY,
    trace_id INTEGER NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (stage IN (
        'authn', 'authz_filter', 'cache_lookup', 'retrieval',
        'post_verify', 'context_build', 'generation', 'ledger', 'response'
    )),
    outcome TEXT NOT NULL,
    timing_ms REAL CHECK (timing_ms IS NULL OR timing_ms >= 0),
    inputs_digest TEXT,
    outputs_digest TEXT,
    scores_json TEXT,
    excerpt TEXT CHECK (excerpt IS NULL OR length(excerpt) <= 200),
    UNIQUE (trace_id, stage)
);

-- The authorization decision set for a trace - plan section 4.2:
-- allowed/denied doc IDs, IDs only (I10: no denied-content excerpts, ever).
CREATE TABLE trace_authz (
    id INTEGER PRIMARY KEY,
    trace_id INTEGER NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('allowed', 'denied')),
    UNIQUE (trace_id, doc_id)
);
