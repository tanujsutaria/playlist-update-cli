# Architecture

The cleaned-up module map after the remediation in `docs/REMEDIATION_PLAN.md`.
Quick orientation lives in `CLAUDE.md`; this file is the detailed reference.

## Layout

`src/` is the **source root** (not an importable package named `src`). Top-level
modules import each other bare; `storage` and `nextgen` are sub-packages.

```
src/
├── main.py                 PlaylistCLI + dispatch_command(registry) + main()
├── arg_parse.py            argparse parser (setup_parsers, parse_tokens, parse_args)
├── interactive_app.py      Textual TUI; calls dispatch_command; OP-1 theme +
│                           background auto listen-sync timer
├── dashboard.py            /dash Textual screen (taste · stats · plays) +
│                           InteractiveBarChart + pure data providers
├── plays.py                play-ledger aggregations over listen_events; the
│                           single home of the 30s play rule (PLAY_MS_THRESHOLD)
├── gdpr_import.py          /import-history: GDPR extended-streaming-history
│                           importer (zip/folder/json -> listen_events)
├── ui.py                   rich rendering helpers (section/table/info/json_output…)
├── models.py               Song, PlaylistHistory, RotationStats, track_id_for()
├── config.py               env_float/int/flag helpers + AppConfig.from_env()
├── song_store.py           SongStore: legacy DB interface over SQLite + embeddings
├── rotation_manager.py     RotationManager: song selection + history (SQLite)
├── scoring.py              PlaylistScoreConfig, MatchScorer, LocalEmbeddingProvider,
│                           WebSearchScoreProvider
├── spotify_manager.py      SpotifyManager (OAuth, retries, playlist ops)
├── taste_facets.py         facet counters for /taste//stats//dash
├── web_search.py           web-search orchestration (_run_command subprocess runner)
├── llm_json.py             shared LLM JSON-parsing helpers
├── anthropic_web_search_wrapper.py   standalone subprocess scripts
├── openai_web_search_wrapper.py      (invoked by absolute path; import llm_json)
├── openai_web_score_wrapper.py
├── storage/
│   ├── db.py               Database (sqlite3 connection, pragmas, session())
│   ├── schema.py           initial_schema..schema_v7 (CREATE statements)
│   ├── migrations.py       ensure_schema(), LATEST_VERSION=7 (idempotent)
│   ├── repos.py            Repositories + per-table repos (artists/tracks/
│   │                       embeddings/playlists/rotation_generations/
│   │                       sync_state/spotify_playlists/liked_tracks/…)
│   ├── vectors.py          encode/decode/norm/normalize float32 vectors
│   ├── sonic.py            sonic feature labels/describers (v5 track_sonic)
│   ├── cache.py            query-hash helper
│   └── legacy_migrate.py   one-time pickle->SQLite migrator (re-embed)
└── nextgen/
    ├── pipeline.py         SearchPipeline (orchestrates a search run)
    ├── providers.py        run_providers -> web_search.run_deep_search
    ├── canonicalize.py     normalize/dedup provider results
    ├── context.py/extract.py  context-card extraction
    ├── embeddings.py       EmbeddingModel (sentence-transformers)
    ├── enrich.py           /enrich: semantic context backfill + re-embed
    ├── acoustic.py         /sonic: AcousticBrainz sonic backfill
    └── scoring.py          SearchScoreConfig, score_candidates, rank_scores

scripts/migrate_legacy.py   CLI wrapper around storage.legacy_migrate
scripts/setup_env.py        one-off environment bootstrap
```

## Data / persistence

Single system of record: **SQLite at `data/tunr.db`** (schema v7). Tables:

- `artists`, `tracks`, `track_context`, `track_embeddings` — track corpus +
  768-dim `all-mpnet-base-v2` vectors.
- `playlists`, `rotation_generations`, `generation_tracks` — rotation history
  (v4; replaces the old `data/history/*.pkl`).
- `queries`, `search_runs`, `search_candidates`, `track_sources` — search cache
  (v6 adds `search_runs.summary` + `search_candidates.metrics_json`).
- `track_sonic` — sonic feature vectors (v5, AcousticBrainz-sourced).
- `listen_events` — the play ledger. v7 adds `ms_played`/`skipped`/`context_uri`
  telemetry (enrich-not-clobber via COALESCE) so live `recently_played` polling
  (`/listen-sync` + the TUI auto-sync) and `/import-history` GDPR exports share
  rows: both mint `event_id = uuid5(bare_base62_id|played_at)`. The canonical
  play rule (`ms_played IS NULL OR ms_played >= 30000`) lives in `plays.py`.
- `sync_state`, `spotify_playlists`, `playlist_tracks`, `liked_tracks` — v7
  read-only library mirror (`/pull`) + per-source sync cursors.
- `schema_version` — migration bookkeeping.

The legacy pickle/numpy store (`data/embeddings/*.pkl|*.npy`) and `db_manager.py`
are retired; data was migrated + re-embedded. Pre-migration snapshots are under
`backups/`.

## Control flow

```
Textual UI (interactive_app)  ──parse_tokens──▶  dispatch_command(cli, name, args)
                                                      │ _COMMAND_HANDLERS[name]
                                                      ▼
                                              PlaylistCLI.<method>
                            ┌─────────────────────────┼───────────────────────────┐
                            ▼                          ▼                           ▼
                       SongStore                 RotationManager              SearchPipeline
                    (repos + model)            (selection + history)        (providers→score→cache)
                            │                          │                           │
                            └──────────────  Repositories / SQLite (data/tunr.db) ─┘
```

- `/update` → `RotationManager.select_songs_for_today()` → `SpotifyManager.refresh_playlist()`
  → append a generation to the SQLite rotation tables.
- `/search` → `SearchPipeline.run()` → `providers.run_providers()` (subprocess to
  Claude/OpenAI) → canonicalize + embed + score → cache in SQLite.

## Tooling

- `make ci` = ruff lint + ruff format --check + mypy + pytest (coverage-gated).
  Mirrored by `.github/workflows/ci.yml` (py3.9–3.12).
- mypy runs a non-strict baseline; legacy modules are in a shrinking ignore list
  in `pyproject.toml`.
