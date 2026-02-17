# Feature Plan & Dependency Graph

## Overview

This document breaks down the 5 features proposed in PR #18 (`proposals/suggested_features.md`) into implementable sub-tasks, maps their internal and cross-feature dependencies, identifies shared infrastructure, and provides a recommended implementation order.

**Features:**
1. Scheduled Auto-Rotation (`/schedule`)
2. Smart Playlist Rules Engine (`/rules`)
3. Listening Analytics Dashboard (`/analytics`)
4. Playlist Fork & Merge (`/fork`, `/merge`)
5. Offline Mode with Sync Queue (`--offline`)

---

## Shared Infrastructure

Before diving into individual features, several sub-tasks are shared across multiple features. Implementing these first reduces duplication and unblocks parallel feature work.

### S1. Schema Migration v4 (shared)

All 5 features require new SQLite tables. A single migration (`schema_v4`) should introduce all new tables at once to avoid multiple migration steps.

**New tables:**

| Table | Used By | Purpose |
|-------|---------|---------|
| `schedules` | F1 (Schedule) | Stores per-playlist schedule definitions |
| `playlist_rules` | F2 (Rules) | Stores per-playlist composition constraints |
| `playlist_snapshots` | F4 (Fork/Merge), F5 (Offline) | Cached full playlist track lists for offline reads and fork source |
| `sync_queue` | F5 (Offline) | Queued write operations for offline mode |

**Files affected:**
- `src/storage/schema.py` — add `schema_v4()` function
- `src/storage/migrations.py` — register v4 migration
- `src/storage/repos.py` — add repository classes: `SchedulesRepo`, `PlaylistRulesRepo`, `PlaylistSnapshotsRepo`, `SyncQueueRepo`

**Dependencies:** None (pure infrastructure).

### S2. Playlist Snapshot Capture (shared)

Multiple features need a cached snapshot of a playlist's current state (track IDs + metadata). This should be a utility that caches the result of `SpotifyManager.get_playlist_tracks()` into the `playlist_snapshots` table.

**Files affected:**
- `src/spotify_manager.py` — add `cache_playlist_snapshot()` method or hook into existing reads
- `src/storage/repos.py` — `PlaylistSnapshotsRepo` (from S1)

**Dependencies:** S1

### S3. Command Registration Pattern (shared)

Features F1-F4 each add 2-4 new slash commands. The existing command dispatch lives in `src/main.py:dispatch_command()` and `src/interactive_app.py`. A consistent pattern for registering new commands prevents the main file from growing further.

**Files affected:**
- `src/main.py` — extend `dispatch_command()` routing
- `src/interactive_app.py` — register commands in TUI
- `src/arg_parse.py` — add argument definitions for new commands

**Dependencies:** None (convention, not a code dependency).

---

## Feature 1: Scheduled Auto-Rotation (`/schedule`)

### Sub-tasks

| ID | Task | Description |
|----|------|-------------|
| F1.1 | `schedules` table + repo | DDL in schema_v4; `SchedulesRepo` with CRUD for schedule records. Columns: `schedule_id`, `playlist_name`, `command`, `args_json`, `interval_seconds`, `last_run_at`, `next_run_at`, `enabled`, `created_at` |
| F1.2 | Schedule manager module | New `src/schedule_manager.py` with logic: create/update/delete schedule, compute `next_run_at`, list schedules, find due schedules |
| F1.3 | `/schedule set` command | Parse `--every <interval>`, `--command <cmd>`, and optional command args. Persist via `SchedulesRepo`. Validate that the target command exists in `dispatch_command()` |
| F1.4 | `/schedule list` command | Query all schedules from SQLite, render as Rich table with columns: playlist, command, interval, last run, next run, enabled |
| F1.5 | `/schedule remove` command | Delete a schedule by playlist name. Confirm before deletion |
| F1.6 | `/schedule run-due` command | Query schedules where `next_run_at < now()`, dispatch each command via `dispatch_command()`, update `last_run_at` and `next_run_at`. Log results. Designed for cron invocation |
| F1.7 | Interval parsing utility | Parse human-friendly intervals (`3d`, `1w`, `12h`, `30m`) into seconds. Reusable by other features |
| F1.8 | Tests | Unit tests for interval parsing, schedule CRUD, due-schedule detection, and `run-due` dispatch (mocked) |

### Internal Dependency Chain

```
S1 (schema v4)
 └─► F1.1 (schedules table + repo)
      └─► F1.2 (schedule manager)
           ├─► F1.3 (/schedule set)
           ├─► F1.4 (/schedule list)
           ├─► F1.5 (/schedule remove)
           └─► F1.6 (/schedule run-due)

F1.7 (interval parsing) ─► F1.3, F1.6
F1.8 (tests) ─► F1.1–F1.7
```

### External Dependencies
- `dispatch_command()` in `src/main.py` — used by F1.6 to execute scheduled commands programmatically.
- No dependency on other features (F2-F5).

---

## Feature 2: Smart Playlist Rules Engine (`/rules`)

### Sub-tasks

| ID | Task | Description |
|----|------|-------------|
| F2.1 | `playlist_rules` table + repo | DDL in schema_v4; `PlaylistRulesRepo` with CRUD. Columns: `rule_id`, `playlist_name`, `rule_type`, `rule_json`, `created_at`, `updated_at` |
| F2.2 | Rule types and evaluation engine | New `src/rules_engine.py`: define rule types (genre_quota, artist_cap, year_range, explicit_filter, popularity_band, duration_range), parse rule JSON, evaluate a candidate track against a rule set. Return accept/reject + reason |
| F2.3 | Constraint-satisfaction integration | Hook into `RotationManager._select_songs_with_history()` (`src/rotation_manager.py:90`). After scoring, iterate candidates and apply rules as post-filters. Accept candidates only while all rules remain satisfiable |
| F2.4 | `/rules set` command | Parse multiple rule flags (`--min-genre`, `--max-per-artist`, `--year-range`, `--no-explicit`, `--popularity`, `--duration`), validate, and persist via `PlaylistRulesRepo` |
| F2.5 | `/rules show` command | Display active rules for a playlist as a Rich table |
| F2.6 | `/rules clear` command | Remove all rules for a playlist |
| F2.7 | `/rules validate` command | Dry-run: load current playlist tracks, evaluate against rules, show which tracks would pass/fail and overall compliance percentage |
| F2.8 | Track metadata resolution | Ensure genre data (from `track_context.fields_json` and `artists.genres_json`), explicit flag, popularity, duration, and release_date are available for rule evaluation. May need to fetch missing metadata from Spotify API for tracks not yet enriched |
| F2.9 | Tests | Unit tests for each rule type evaluation, constraint-satisfaction logic, edge cases (empty rules, unsatisfiable rules, no candidates pass) |

### Internal Dependency Chain

```
S1 (schema v4)
 └─► F2.1 (playlist_rules table + repo)
      └─► F2.2 (rule types + evaluation engine)
           ├─► F2.3 (integration with RotationManager)
           ├─► F2.4 (/rules set)
           ├─► F2.5 (/rules show)
           ├─► F2.6 (/rules clear)
           └─► F2.7 (/rules validate)

F2.8 (metadata resolution) ─► F2.2, F2.3, F2.7
F2.9 (tests) ─► F2.1–F2.8
```

### External Dependencies
- `RotationManager._select_songs_with_history()` in `src/rotation_manager.py` — integration point for constraint application.
- `SpotifyManager` for fetching missing track metadata (F2.8).
- `track_context.fields_json`, `artists.genres_json` in SQLite (existing data).
- **Cross-feature:** F4 (Fork/Merge) can optionally use the rules engine as a filter during fork operations.

---

## Feature 3: Listening Analytics Dashboard (`/analytics`)

### Sub-tasks

| ID | Task | Description |
|----|------|-------------|
| F3.1 | Analytics query module | New `src/analytics.py`: pure SQL query functions against `listen_events`, `tracks`, `artists`, `search_runs`, `search_candidates`, and `PlaylistHistory` data. Returns structured dicts, not UI objects |
| F3.2 | Listening overview queries | Total plays, unique tracks, top genres, listening streak (consecutive days with plays), discovery rate (% of played tracks added via `/search`) |
| F3.3 | Genre distribution queries | Genre counts from `artists.genres_json` joined with `listen_events` for a given time window. Return sorted genre → count mapping |
| F3.4 | Rotation health queries | Per-playlist: current generation number, average plays before rotation, % of songs never rotated in, rotation coverage, freshness score. Requires joining `PlaylistHistory` generations with `listen_events` |
| F3.5 | Discovery funnel queries | Count search_runs → search_candidates → tracks with status='added' → tracks with listen_events. Aggregate over a time window |
| F3.6 | Top artists queries | Most-played artists with listen counts and genre breakdown from `listen_events` joined with `artists` |
| F3.7 | Terminal rendering (Rich) | Render each analytics section as Rich panels/tables. Support `--days <N>` time window flag |
| F3.8 | Chart export (matplotlib) | Optional `--export png`/`--export html` flag. Generate matplotlib charts for genre pie, listening timeline, discovery funnel bar chart |
| F3.9 | `/analytics` command routing | Subcommand dispatch: `overview`, `genres`, `rotation`, `discovery`, `artists`, `export`. Register in `dispatch_command()` and TUI |
| F3.10 | Tests | Unit tests for query functions with pre-populated test databases. Test edge cases: no listen data, empty playlists, zero discovery |

### Internal Dependency Chain

```
F3.1 (analytics query module)
 ├─► F3.2 (listening overview)
 ├─► F3.3 (genre distribution)
 ├─► F3.4 (rotation health)
 ├─► F3.5 (discovery funnel)
 └─► F3.6 (top artists)

F3.7 (terminal rendering) ─► F3.2–F3.6
F3.8 (chart export) ─► F3.2–F3.6
F3.9 (command routing) ─► F3.7, F3.8
F3.10 (tests) ─► F3.1–F3.6
```

### External Dependencies
- **Existing tables only** — no new schema required. Reads from: `listen_events` (schema_v2), `tracks`, `artists`, `track_context`, `search_runs`, `search_candidates`.
- `PlaylistHistory` data (from `src/rotation_manager.py` pickle files or SQLite depending on migration status).
- `matplotlib` — already a dependency in `pyproject.toml`.
- `Rich` — already used throughout the app.
- **No cross-feature dependencies** — can be implemented independently.

---

## Feature 4: Playlist Fork & Merge (`/fork`, `/merge`)

### Sub-tasks

| ID | Task | Description |
|----|------|-------------|
| F4.1 | Fork/merge module | New `src/fork_merge.py`: core logic for fork (filter + copy) and merge (combine + dedup + strategy) operations |
| F4.2 | Fork filter engine | Apply filters to a list of tracks: genre filter, audio feature filters (energy, tempo, valence, acousticness), year range, popularity band, explicit flag, query-based embedding similarity filter |
| F4.3 | Merge strategies | Implement merge strategies: `interleave`, `top-scored`, `round-robin`, `weighted`. Each takes N track lists + config and returns a merged list |
| F4.4 | Audio features fetcher | Fetch Spotify audio features (energy, tempo, valence, acousticness, danceability) for tracks. Cache in a new column or table. Uses `SpotifyManager` + `sp.audio_features()` |
| F4.5 | `/fork` command | Parse: source playlist, target name, filter flags. Fetch source tracks via `SpotifyManager.get_playlist_tracks()`, apply filters, create target playlist via `SpotifyManager.create_playlist()` + `playlist_add_items()`, record `PlaylistHistory` |
| F4.6 | `/merge` command | Parse: N source playlists, `--into` target, `--strategy`, `--dedup`, `--count`. Fetch all source track lists, apply merge strategy, deduplicate by `track_id`, create/update target playlist |
| F4.7 | Embedding-based query filter | For `--query "uplifting and melodic"` fork filter: embed the query, compute cosine similarity against track embeddings, filter by threshold. Reuses `src/nextgen/pipeline.py` scoring |
| F4.8 | Playlist snapshot integration | After fork/merge, cache the new playlist snapshot (S2). Use cached snapshots as source when source playlist was recently viewed |
| F4.9 | Tests | Unit tests for each merge strategy, fork filters, dedup logic, audio feature filtering. Integration tests with mocked Spotify API |

### Internal Dependency Chain

```
S1 (schema v4) ─► S2 (snapshot capture)

F4.1 (fork/merge module)
 ├─► F4.2 (fork filter engine) ─► F4.5 (/fork command)
 ├─► F4.3 (merge strategies) ─► F4.6 (/merge command)
 └─► F4.4 (audio features fetcher) ─► F4.2

F4.7 (embedding query filter) ─► F4.2
S2 (snapshot) ─► F4.8 (snapshot integration) ─► F4.5, F4.6
F4.9 (tests) ─► F4.1–F4.8
```

### External Dependencies
- `SpotifyManager.get_playlist_tracks()`, `create_playlist()`, `playlist_add_items()` in `src/spotify_manager.py`.
- `sp.audio_features()` — Spotify API (may require scope check).
- `src/nextgen/pipeline.py` scoring for embedding-based query filter (F4.7).
- `src/scoring.py` for `top-scored` merge strategy.
- **Cross-feature:** Can optionally use F2 (Rules Engine) as a fork filter or merge constraint.

---

## Feature 5: Offline Mode with Sync Queue (`--offline`)

### Sub-tasks

| ID | Task | Description |
|----|------|-------------|
| F5.1 | `sync_queue` table + repo | DDL in schema_v4; `SyncQueueRepo` with CRUD. Columns: `queue_id`, `command`, `args_json`, `created_at`, `status` (pending/failed/completed), `error`, `attempted_at`, `completed_at` |
| F5.2 | Offline mode flag + detection | Add `--offline` global flag to CLI. Auto-detect: if Spotify health check fails on startup, offer to activate offline mode. Store offline state in app context |
| F5.3 | SpotifyManager write interceptor | Wrap write methods (`create_playlist`, `playlist_add_items`, `playlist_remove_items`, `refresh_playlist`) with a queue interceptor. When offline, serialize the operation into `sync_queue` instead of executing |
| F5.4 | Read-from-cache layer | For read operations (`get_playlist_tracks`, `view`), serve from `playlist_snapshots` table when offline. For `/stats`, already fully local. For `/search`, works if LLM API available |
| F5.5 | `/sync-queue` command | Subcommands: `list` (show pending operations), `flush` (execute all pending), `drop <id>` (discard), `retry` (retry failed). `flush` iterates queue in order, dispatches via `dispatch_command()`, updates status |
| F5.6 | Snapshot auto-capture | After every successful Spotify read (`get_playlist_tracks`, `/view`), auto-cache the snapshot in `playlist_snapshots`. This builds the offline read cache over time |
| F5.7 | Offline indicator in UI | Show `[OFFLINE]` badge in the TUI status bar when offline mode is active. Show queued operation count |
| F5.8 | Conflict detection | When flushing the sync queue, detect if the playlist has changed on Spotify since the operation was queued (compare snapshot timestamps). Warn user of potential conflicts |
| F5.9 | Tests | Unit tests for queue CRUD, interceptor routing, flush execution, conflict detection. Mock Spotify connectivity |

### Internal Dependency Chain

```
S1 (schema v4)
 ├─► F5.1 (sync_queue table + repo)
 │    └─► F5.3 (write interceptor) ─► F5.5 (/sync-queue commands)
 │                                     └─► F5.8 (conflict detection)
 └─► S2 (snapshot capture)
      └─► F5.4 (read-from-cache) ─► F5.6 (auto-capture)

F5.2 (offline flag + detection) ─► F5.3, F5.4, F5.7
F5.7 (UI indicator) ─► F5.2
F5.9 (tests) ─► F5.1–F5.8
```

### External Dependencies
- `SpotifyManager` write methods — intercepted in offline mode.
- `dispatch_command()` in `src/main.py` — used by flush to replay queued operations.
- `playlist_snapshots` table — shared with S2 and F4.
- **Cross-feature:** S2 (Playlist Snapshot) is shared with F4 (Fork/Merge). F1 (Schedule) benefits from offline mode — scheduled commands can queue when offline and flush when connectivity returns.

---

## Cross-Feature Dependency Graph

```
                    ┌──────────────────┐
                    │  S1. Schema v4   │
                    │  (all new tables)│
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┬──────────────┐
              │              │              │              │
              ▼              ▼              ▼              │
    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
    │ F1.Schedule │  │ F2.Rules    │  │ S2.Snapshot  │    │
    │ (table+repo)│  │ (table+repo)│  │ (capture)    │    │
    └──────┬──────┘  └──────┬──────┘  └──────┬───────┘    │
           │                │           ┌────┴────┐       │
           ▼                ▼           ▼         ▼       ▼
    ┌─────────────┐  ┌─────────────┐  ┌────┐  ┌──────────────┐
    │ F1.Commands │  │ F2.Engine + │  │ F4 │  │ F5.Offline   │
    │ /schedule   │  │ Integration │  │Fork│  │ Mode + Queue │
    │ set/list/   │  │ w/ Rotation │  │  & │  │ --offline    │
    │ remove/     │  │ Manager     │  │Merge│  │ /sync-queue  │
    │ run-due     │  │             │  │    │  │              │
    └─────────────┘  └──────┬──────┘  └──┬─┘  └──────────────┘
                            │            │
                            ▼            │
                     ┌─────────────┐     │
                     │  Optional:  │◄────┘
                     │  F4 can use │
                     │  F2 rules   │
                     │  as filters │
                     └─────────────┘

    ┌─────────────────────────────────────────────────┐
    │ F3. Analytics (fully independent — reads only   │
    │ from existing tables: listen_events, tracks,    │
    │ artists, search_runs, search_candidates)        │
    └─────────────────────────────────────────────────┘
```

### Dependency Summary Table

| Component | Depends On | Blocks |
|-----------|-----------|--------|
| **S1** (Schema v4) | — | F1.1, F2.1, S2, F5.1 |
| **S2** (Snapshot Capture) | S1 | F4.8, F5.4, F5.6 |
| **F1** (Schedule) | S1 | — |
| **F2** (Rules Engine) | S1 | F4 (optional) |
| **F3** (Analytics) | — | — |
| **F4** (Fork/Merge) | S2, optionally F2 | — |
| **F5** (Offline) | S1, S2 | — |

### Cross-Feature Integration Points

| Integration | Features | Description |
|-------------|----------|-------------|
| Scheduled offline flush | F1 + F5 | `run-due` could auto-flush the sync queue before executing scheduled commands |
| Rules as fork filter | F2 + F4 | `/fork` could accept `--apply-rules` to use the playlist's rules engine during filtering |
| Analytics for schedules | F1 + F3 | `/analytics` could report schedule execution history and success rates |
| Offline schedule queue | F1 + F5 | When offline, scheduled commands queue into `sync_queue` instead of executing |
| Snapshot for analytics | S2 + F3 | Playlist snapshots could provide point-in-time data for trend analysis |

---

## Recommended Implementation Order

### Phase A: Foundation (implement first)

| Order | Item | Rationale |
|-------|------|-----------|
| A.1 | **S1 — Schema v4** | Unblocks all features needing new tables |
| A.2 | **F3 — Analytics** | Zero dependencies on new schema, reads existing tables only. Delivers immediate user value. Good warmup task |
| A.3 | **S2 — Snapshot Capture** | Small utility, unblocks F4 and F5 |

### Phase B: Independent Features (parallelizable)

These three features have no dependencies on each other and can be developed in parallel:

| Order | Item | Rationale |
|-------|------|-----------|
| B.1 | **F1 — Schedule** | Self-contained, depends only on S1. Straightforward CRUD + cron model |
| B.2 | **F2 — Rules Engine** | Self-contained, depends only on S1. Core logic is pure functions (testable). Integration with RotationManager is a clean hook |
| B.3 | **F4 — Fork/Merge** | Depends on S2. Core merge strategies are pure functions. Fork filters reuse existing metadata |

### Phase C: Complex Integration (implement last)

| Order | Item | Rationale |
|-------|------|-----------|
| C.1 | **F5 — Offline Mode** | Most complex feature. Depends on S1 + S2. Requires interceptor pattern around SpotifyManager (wide blast radius). Benefits from F1 and F4 being stable |
| C.2 | **Cross-feature integrations** | Wire up optional connections: F2 rules in F4 forks, F1 schedule + F5 offline flush, F3 analytics extensions |

### Visual Timeline

```
Phase A (sequential):     S1 ──► F3 ──► S2
                                         │
Phase B (parallel):       F1 ◄───────────┤
                          F2 ◄───────────┤
                          F4 ◄───────────┘
                           │    │    │
Phase C (integration):     └────┴────┴──► F5 ──► Cross-feature wiring
```

---

## Estimated Complexity

| Feature | New Files | Modified Files | New Tables | New Commands | Complexity |
|---------|-----------|---------------|------------|-------------|------------|
| S1 (Schema v4) | 0 | 3 | 4 | 0 | Low |
| S2 (Snapshot) | 0 | 2 | 0 (uses S1) | 0 | Low |
| F1 (Schedule) | 1 | 3 | 1 (uses S1) | 4 | Medium |
| F2 (Rules) | 1 | 3 | 1 (uses S1) | 4 | Medium-High |
| F3 (Analytics) | 1 | 3 | 0 | 6 | Medium |
| F4 (Fork/Merge) | 1 | 3 | 0 (uses S1) | 2 | Medium-High |
| F5 (Offline) | 1 | 4 | 1 (uses S1) | 4 | High |

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| `main.py` grows further (already 101KB) | Maintainability | New features should live in dedicated modules; `dispatch_command()` only adds routing entries |
| SpotifyManager interceptor (F5) breaks existing flows | High | Thorough integration testing; interceptor should be a clean wrapper, not inline modifications |
| Rules engine makes rotation selection too slow | Medium | Rules are post-filters on an already-scored list; evaluate lazily and short-circuit on first rule violation |
| Audio features API rate limits (F4) | Medium | Batch requests (max 100 IDs per call), cache results in SQLite |
| Offline mode edge cases | High | Start with queue-only (no auto-detect), add auto-detect later. Extensive conflict detection testing |
| PlaylistHistory pickle vs SQLite inconsistency | Medium | F3/F4 should handle both formats gracefully or migrate during Phase A |

---

## File Impact Summary

### New Files
| File | Feature | Purpose |
|------|---------|---------|
| `src/schedule_manager.py` | F1 | Schedule CRUD and due-check logic |
| `src/rules_engine.py` | F2 | Rule types, evaluation, constraint satisfaction |
| `src/analytics.py` | F3 | Analytics query functions and rendering |
| `src/fork_merge.py` | F4 | Fork filter engine, merge strategies |
| `src/offline.py` | F5 | Offline mode management, write interceptor |

### Modified Files (by frequency)
| File | Modified By | Changes |
|------|-------------|---------|
| `src/storage/schema.py` | S1 | Add `schema_v4()` |
| `src/storage/migrations.py` | S1 | Register v4 |
| `src/storage/repos.py` | S1 | Add 4 new repo classes |
| `src/main.py` | F1-F5 | Add command routing entries |
| `src/interactive_app.py` | F1-F5 | Register TUI commands |
| `src/arg_parse.py` | F1-F5 | Add argument definitions |
| `src/rotation_manager.py` | F2 | Hook rules engine into selection |
| `src/spotify_manager.py` | F4, F5, S2 | Audio features, snapshot, interceptor |
| `src/ui.py` | F3, F5 | Analytics rendering, offline badge |

### New Test Files
| File | Feature |
|------|---------|
| `tests/test_schedule_manager.py` | F1 |
| `tests/test_rules_engine.py` | F2 |
| `tests/test_analytics.py` | F3 |
| `tests/test_fork_merge.py` | F4 |
| `tests/test_offline.py` | F5 |
