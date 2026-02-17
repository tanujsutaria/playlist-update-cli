# Suggested Features for Tunr

Based on a comprehensive analysis of the codebase architecture, current capabilities, storage layer, API integrations, CLI structure, error handling, testing, and known limitations, here are 5 high-value feature proposals.

---

## 1. Scheduled Auto-Rotation (`/schedule` command)

### Problem
Playlist rotation currently requires manual invocation via `/rotate` or `/update`. Users who want their playlists to stay fresh must remember to run commands regularly. There is no automation, cron integration, or background scheduling.

### Proposed Solution
Add a `/schedule` command that lets users define rotation schedules per playlist, persisted in SQLite, and executed by a lightweight background daemon or system cron integration.

**New commands:**
- `/schedule set "My Playlist" --every 3d --command rotate --policy played` — rotate every 3 days
- `/schedule set "Discover Weekly Mirror" --every 1w --command update --count 15 --score-strategy hybrid` — weekly update with hybrid scoring
- `/schedule list` — show all active schedules
- `/schedule remove "My Playlist"` — cancel a schedule
- `/schedule run-due` — execute all overdue schedules (designed for cron: `*/30 * * * * tunr schedule run-due`)

**Implementation approach:**
- New `schedules` table in SQLite: `playlist_name, command, args_json, interval_seconds, last_run_at, next_run_at, enabled`
- `run-due` subcommand checks `next_run_at < now()`, dispatches the stored command through existing `dispatch_command()`, and updates timestamps
- Users add `tunr schedule run-due` to system cron or systemd timer
- No long-running daemon needed — stateless check-and-run model

**Why it fits:**
- The `dispatch_command()` router already supports programmatic invocation (`src/main.py:2214`)
- SQLite persistence layer (`src/storage/`) is mature with migrations support
- All rotation/update commands already work non-interactively
- Listen-sync (`/listen-sync`) would also benefit from scheduling

---

## 2. Smart Playlist Rules Engine (`/rules` command)

### Problem
Playlist composition is currently controlled only by `--count`, `--fresh-days`, and `--score-strategy` flags. Users cannot express declarative constraints like "at least 30% jazz," "max 2 songs per artist," or "nothing before 2015." The scoring pipeline (`src/nextgen/scoring.py`) ranks candidates but has no constraint-satisfaction layer.

### Proposed Solution
Add a rules engine that lets users define per-playlist composition constraints, evaluated during song selection in `RotationManager.select_songs_for_today()`.

**New commands:**
- `/rules set "My Playlist" --min-genre jazz:30% --max-per-artist 2 --year-range 2015-2026 --no-explicit`
- `/rules show "My Playlist"` — display active rules
- `/rules clear "My Playlist"` — remove all rules
- `/rules validate "My Playlist"` — dry-run check against current playlist

**Rule types:**
| Rule | Example | Enforcement |
|------|---------|-------------|
| Genre quota | `--min-genre jazz:30%` | At least 30% of selected songs must have "jazz" in their context genres |
| Artist cap | `--max-per-artist 2` | No more than 2 songs from the same artist per rotation |
| Year range | `--year-range 2015-2026` | Only include songs released within range |
| Explicit filter | `--no-explicit` | Exclude tracks marked explicit on Spotify |
| Popularity band | `--popularity 20-70` | Only include tracks within Spotify popularity range |
| Duration range | `--duration 2m-6m` | Filter by track length |

**Implementation approach:**
- New `playlist_rules` table: `playlist_name, rule_type, rule_json, created_at`
- Constraint-satisfaction pass inserted between candidate scoring and final selection in `RotationManager._select_songs_with_history()` (`src/rotation_manager.py:90`)
- Rules evaluated as post-filters: score-ranked candidates are iterated, and each candidate is accepted only if all rules remain satisfiable
- Genre data already available in `track_context.fields_json` and `artists.genres_json`
- Track metadata (explicit, popularity, duration, release_date) already stored in `tracks` table

**Why it fits:**
- Track context, artist genres, and Spotify metadata are already persisted in SQLite
- The scoring pipeline already produces ranked candidates — rules act as a constraint layer on top
- `ScoreConfig` in `src/nextgen/scoring.py` already supports weight-based tuning; rules extend this to hard constraints

---

## 3. Listening Analytics Dashboard (`/analytics` command)

### Problem
The existing `/stats` command shows basic counts (total songs, generations, rotation progress). The `listen_events` table tracks play history and the `search_runs` table logs searches, but there is no way to visualize listening patterns, genre trends, rotation effectiveness, or discovery success rates. Matplotlib is already a dependency but barely used.

### Proposed Solution
Add an `/analytics` command that generates rich terminal-based analytics and optional chart exports.

**New commands:**
- `/analytics overview` — high-level dashboard: total listens, unique tracks, top genres, listening streak
- `/analytics genres --days 30` — genre distribution pie chart (rendered as Rich table + optional PNG export)
- `/analytics rotation "My Playlist"` — rotation effectiveness: how many songs get played before being rotated out, average generation lifespan
- `/analytics discovery --days 90` — discovery funnel: searched → validated → added → actually played
- `/analytics artists --top 20` — most-played artists with listen counts and genre breakdown
- `/analytics export --format html --output report.html` — export full report

**Dashboard sections:**

```
Listening Overview (last 30 days)
├── Total plays: 847
├── Unique tracks: 312
├── Top genre: Indie Rock (23%)
├── Listening streak: 14 days
└── Discovery rate: 18% of played tracks were added via /search

Rotation Health: "Evening Chill"
├── Current generation: #12
├── Avg plays before rotation: 4.2
├── Songs never rotated in: 45 (28%)
├── Rotation coverage: 72%
└── Freshness score: 8.1/10

Discovery Funnel (last 90 days)
├── Web searches run: 23
├── Candidates found: 461
├── Passed validation: 287 (62%)
├── Added to playlists: 134 (47%)
└── Actually played: 89 (66%)
```

**Implementation approach:**
- Query `listen_events` joined with `tracks` and `artists` for listening patterns
- Query `search_runs` + `search_candidates` for discovery funnel
- Query `PlaylistHistory` generations for rotation metrics
- Render with Rich tables for terminal display; matplotlib for PNG/HTML export
- New `src/analytics.py` module with pure query + rendering functions

**Why it fits:**
- `listen_events` table already captures play history (`src/storage/schema.py`)
- `search_runs` and `search_candidates` already track the discovery pipeline
- `PlaylistHistory` generations provide rotation data
- matplotlib is already a dependency (declared in `pyproject.toml`)
- Rich tables and panels are already used extensively throughout `src/ui.py`

---

## 4. Playlist Fork & Merge (`/fork`, `/merge` commands)

### Problem
Users cannot combine playlists, create filtered copies, or branch a playlist into variations. The only way to create a new playlist is via `/update` with a fresh name or manual Spotify operations. There is no way to say "take the best of Playlist A and Playlist B" or "create a chill version of my workout playlist."

### Proposed Solution
Add `/fork` and `/merge` commands for playlist composition operations.

**New commands:**
- `/fork "Evening Chill" "Late Night Chill" --filter-genre ambient,downtempo --max 30` — create a filtered copy
- `/fork "Workout Mix" "Cardio Only" --min-energy 0.7 --min-tempo 120` — fork with audio feature filters
- `/merge "Jazz Favorites" "Blues Favorites" --into "Jazz & Blues" --dedup --count 50` — combine two playlists, deduplicate, limit to 50
- `/merge "Playlist A" "Playlist B" "Playlist C" --into "Best Of" --strategy top-scored --count 40` — merge multiple playlists, keeping highest-scored tracks

**Merge strategies:**
- `interleave` — alternate tracks from each source playlist
- `top-scored` — re-score all candidates and pick the best (uses existing scoring pipeline)
- `round-robin` — equal representation from each source
- `weighted` — user-specified weights per source (`--weight "Jazz:0.6,Blues:0.4"`)

**Fork filters:**
- Genre filters (from `track_context.fields_json` or `artists.genres_json`)
- Audio feature filters: energy, tempo, valence, acousticness (from Spotify audio features, already fetched in `src/scoring.py:180`)
- Year range, popularity band, explicit flag
- Query-based filter: `--query "uplifting and melodic"` (uses existing embedding similarity)

**Implementation approach:**
- Fork: fetch source playlist tracks via `SpotifyManager.get_playlist_tracks()`, apply filters, create new playlist via `SpotifyManager.create_playlist()` + `playlist_add_items()`
- Merge: fetch all source playlists, apply dedup by `track_id`, apply merge strategy, create or update target playlist
- Both operations create a new `PlaylistHistory` entry for the target
- Reuse `SearchPipeline` scoring for `--query` based filtering and `top-scored` merge strategy

**Why it fits:**
- `SpotifyManager` already has all playlist CRUD operations (`src/spotify_manager.py`)
- Audio features are already fetchable via `audio_features()` in `src/scoring.py`
- Track context and genre data are already stored in SQLite
- Embedding-based filtering reuses `src/nextgen/pipeline.py` scoring
- Playlist history tracking automatically extends to forked/merged playlists

---

## 5. Offline Mode with Sync Queue (`--offline` flag)

### Problem
Every playlist operation requires a live Spotify API connection. Network failures, rate limiting, or expired tokens cause immediate errors. There is no way to queue operations for later execution, preview changes without API access, or work with cached data when offline. The retry mechanism (`_retry_with_backoff` in `src/spotify_manager.py:18`) handles transient errors but not prolonged outages.

### Proposed Solution
Add an offline mode that queues write operations and executes them when connectivity is restored, while allowing read operations against the local SQLite cache.

**New commands and flags:**
- `--offline` global flag — queue all Spotify writes, serve reads from cache
- `/sync-queue` — show pending queued operations
- `/sync-queue flush` — execute all queued operations now
- `/sync-queue drop [id]` — discard a queued operation
- `/sync-queue retry` — retry failed operations

**Behavior in offline mode:**
| Operation | Offline Behavior |
|-----------|-----------------|
| `/view` | Serve from last-cached playlist state in SQLite |
| `/stats` | Fully local — already works without Spotify |
| `/search` | Works if LLM API available; results cached locally |
| `/update` | Select songs locally, queue Spotify write, show preview |
| `/rotate` | Compute rotation locally, queue Spotify write |
| `/ingest` | Queue for later execution |
| `/listen-sync` | Queue for later execution |

**Implementation approach:**
- New `sync_queue` table: `queue_id, command, args_json, created_at, status (pending/failed/completed), error, attempted_at`
- Wrap `SpotifyManager` write methods with a queue interceptor when `--offline` is active
- `flush` command iterates pending queue items in order, calling `dispatch_command()` for each
- Auto-detect offline: if initial Spotify health check fails, offer to switch to offline mode
- Read operations pull from `tracks`, `artists`, and cached playlist snapshots in SQLite

**Additional enhancement — Playlist Snapshot Cache:**
- After each successful `/view` or `/update`, cache the full playlist track list in a new `playlist_snapshots` table: `playlist_name, snapshot_json, captured_at`
- Offline `/view` serves from the latest snapshot
- Enables offline diffing: `/diff` can compare proposed changes against cached snapshot

**Why it fits:**
- SQLite already stores comprehensive track/artist/context data locally
- `dispatch_command()` already supports programmatic command execution
- The search pipeline already caches results in SQLite (`search_runs`, `search_candidates`)
- Rotation and scoring are already local-first — only the final Spotify write needs connectivity
- Error handling infrastructure exists for retry/backoff patterns
