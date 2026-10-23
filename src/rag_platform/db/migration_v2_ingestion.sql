-- Phase 4 ingestion corpus tables - migration v2 (plan v2.4 sections 15
-- Phase 4 "Ingestion: loaders, chunker, injection scan, declared-date
-- capture | Corpus on D:", 4.1 metadata timestamps, 6 storage choices).
--
-- Design notes:
-- - Identity is content-derived and stable: documents.doc_id = sha256 of
--   (source_path, content_sha256); chunks.chunk_id = sha256 of (doc_id,
--   ordinal). Re-ingesting identical content is a no-op; changed content at
--   the same path atomically replaces the previous version (source_path is
--   UNIQUE and ON DELETE CASCADE removes the old chunks).
-- - declared_date, ingested_at and source_mtime are three DISTINCT concepts
--   (plan section 4.1): the author-declared `date:` field, the ingestion
--   clock (injectable for deterministic tests), and file metadata. A
--   declared date is NEVER inferred from mtime. declared_date_status
--   records the honest outcome for missing/malformed/ambiguous declarations.
-- - Injection scanning is advisory (plan invariants I8/I11): the original
--   chunk content is always preserved verbatim; only a quarantine flag and
--   a bounded findings JSON are stored alongside it.
-- - chunk length CHECK encodes the plan v1 section 6.2 hard max chunk size
--   (2000 chars) as defense in depth; the chunker itself enforces it too.
-- - chunk_fts (migration v1) stays a standalone FTS5 index and is populated
--   in the same transaction as these tables.

CREATE TABLE documents (
    doc_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,          -- relative to project root, POSIX-style
    source_name TEXT NOT NULL,                 -- basename only (I16-style safe logging)
    format TEXT NOT NULL CHECK (format IN ('txt', 'md', 'pdf')),
    content_sha256 TEXT NOT NULL,              -- sha256 of the extracted text (UTF-8)
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    source_mtime TEXT,                         -- file metadata (ISO-8601 UTC) - not a declared date
    declared_date TEXT,                        -- ISO calendar date YYYY-MM-DD or NULL
    declared_date_status TEXT NOT NULL CHECK (
        declared_date_status IN ('ok', 'missing', 'malformed', 'ambiguous')
    ),
    ingested_at TEXT NOT NULL,                 -- ingestion clock (ISO-8601 UTC), distinct from both
    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 1)
);

CREATE TABLE chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    content TEXT NOT NULL CHECK (length(content) >= 1 AND length(content) <= 2000),
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    -- char_start/char_end are Unicode code-point offsets into the EXTRACTED
    -- text (Python str indices; SQLite length() counts code points too, so
    -- the length() check above measures the same unit). For txt/md that is
    -- the decoded file content; for PDFs it is a lossy page-text
    -- reconstruction (pages joined, whitespace-only pages dropped), so these
    -- offsets never identify PDF byte positions or visual layout. They are
    -- document-relative - unlike injection_findings offsets, which are
    -- chunk-relative (index the content column of this row).
    char_end INTEGER NOT NULL CHECK (char_end > char_start),
    injection_quarantined INTEGER NOT NULL CHECK (injection_quarantined IN (0, 1)),
    injection_findings TEXT,                   -- bounded JSON array; NULL when clean
    UNIQUE (doc_id, ordinal)
);

CREATE INDEX chunks_doc_id_idx ON chunks(doc_id);
