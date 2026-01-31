# Tunr Next-Gen Roadmap (Greenfield)

## Context
This roadmap replaces the current pickle/CSV-centric workflow with a greenfield SQLite-backed pipeline.
No backward compatibility is required. LLMs are used only for discovery + context extraction.
Similarity is computed locally via sentence embeddings. Rich context is hidden in the UI and exposed only via `/debug`.

## Principles
- **Cache-first**: if it exists locally, never re-search.
- **Strict-by-default**: only use sourced context; fallback to lenient when strict is insufficient.
- **Local similarity**: no LLM-based similarity; audio features are optional and not required.
- **Hidden richness**: rich context for embeddings, minimal UI output, `/debug` for audit.
- **Deterministic**: consistent hashing, scoring, and reproducible results.

## Phase 0 — Decisions
- Choose embedding model (`all-mpnet-base-v2` vs `all-MiniLM-L12-v2`).
- Lock strict/lenient thresholds (confidence + minimum strict tokens).
- Finalize UI columns for `/search` result table.
- Confirm provider schema for context extraction (JSON contract).

## Phase 1 — Storage Foundation (Detailed Task List)
**Goal:** SQLite as the single source of truth, with query/track/context/embedding caching.

**Schema + migrations**
- Define SQLite schema (tables: `tracks`, `artists`, `track_context`, `track_embeddings`, `queries`,
  `search_runs`, `search_candidates`, `track_sources`).
- Add a schema bootstrap/migration runner.
- Add indices for `spotify_id`, `artist_id`, `query_hash`, `track_id`.

**Storage module structure**
- Create `src/storage/`:
  - `db.py` (connection + PRAGMA + WAL)
  - `schema.py` (DDL)
  - `migrations.py` (versioned changes)
  - `repos.py` (CRUD for tracks, contexts, embeddings, queries, runs)
  - `vectors.py` (embed BLOB encode/decode, norm helpers)

**Query cache**
- Add query hashing + normalization.
- Store query embeddings and constraints in `queries`.
- Add cache lookup: query hash → candidate IDs + scores.

**Debug hooks**
- Wire `/debug last` and `/debug track <id|rank>` to SQLite.
- Add an internal debug data model: last run summary + sources + strict/lenient fields.

**Tests**
- Schema tests: create DB, ensure tables + indices exist.
- Repository tests: insert/read/update for each entity.
- Cache tests: query hash determinism, query→candidate lookup.

## Phase 2 — Provider Pipeline + Context Extraction
**Goal:** LLMs provide discovery + context only, stored with sources.

- Create provider runner (parallelizable) that outputs a strict JSON schema.
- Add dedupe + canonicalization (track+artist normalization).
- Persist raw sources + snippets into `track_sources`.
- Build strict/lenient extraction pipeline:
  - Strict requires sources.
  - Lenient requires confidence threshold.
  - Record per-field strict/lenient flags + confidence in `track_context.fields_json`.
- Build rich context card and store `strict_text`, `lenient_text`, `context_text`.

## Phase 3 — Local Embeddings + Scoring
**Goal:** local semantic similarity; no LLM scoring.

- Add local embedding model loader (sentence-transformers).
- Batch embed `context_text`; store in `track_embeddings`.
- Embed queries locally and store in `queries`.
- Implement scoring:
  - cosine similarity on embeddings
  - strict/lenient weighted score
  - optional metadata boosts (genre overlap, year proximity).
- Store run scores in `search_candidates` for reproducibility.

## Phase 4 — UX + Streaming UI
**Goal:** visible progress, hidden richness, fast cache hits.

- Add progress stages: search → extract → embed → score → cache.
- Stream table rows as candidates become ready.
- Show minimal columns: score, sources count, provider count, status.
- Keep rich context hidden; `/debug` exposes it.

## Phase 5 — Command Consolidation
**Goal:** reduce surface area; make `/search` the primary entry point.

- Default workflow: `/search` always caches (no prompt).
- Keep optional CSV import, but remove from primary UX.
- Add `/ingest` (liked tracks, playlist, top tracks) as non-CSV defaults.

## Phase 6 — Rotation Rethink
**Goal:** playlist rotation based on play history and cached similarity.

- Build local listen ledger (recently played polling).
- Implement “played since added” rotation policy.
- Rank candidates using cached semantic similarity + metadata.
- Preserve playlist ID (no delete/recreate).

## Deliverables Summary
- SQLite storage + repositories.
- Strict/lenient context extraction with sources.
- Local embedding and scoring pipeline.
- Streamed UI with debug-only deep context.
- Consolidated commands and ingestion paths.
