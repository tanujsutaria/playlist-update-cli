# Agent Notes (playlist-update-cli)

**`CLAUDE.md` is the canonical orientation file** (commands, architecture,
conventions, gotchas). This file holds the safety/state notes.

## Project summary
- CLI tool for Spotify playlist management with rotation, similarity, and history tracking.
- Entry point: `tunr` launches the interactive Textual UI (`main:main`).
- Core modules: `src/main.py`, `src/song_store.py`, `src/spotify_manager.py`,
  `src/rotation_manager.py`, `src/models.py`, plus `src/storage/` and `src/nextgen/`.
- State lives in SQLite at `data/tunr.db` (system of record) and `.spotify_cache/` (tokens).

## Safety + state
- Avoid modifying `data/`, `backups/`, `imports/`, or `.spotify_cache/` unless explicitly asked.
- Never read or print `.spotify_cache/**` or `config/.env*` (secrets).
- Do not run Spotify API calls or OAuth flows unless the user requests it.
- `WEB_SEARCH_*` / `WEB_SCORE_*` env vars execute arbitrary local commands — trusted input only.
- Prefer read-only inspection when exploring history or embeddings.

## Common commands
- Install deps + hooks: `make install` (or `uv pip install -e '.[dev]'`)
- Run app: `tunr` (needs a TTY — Textual UI)
- Tests / lint / types / full gate: `make test` / `make lint` / `make typecheck` / `make ci`
- One-time legacy migration (already run): `python scripts/migrate_legacy.py`

## Repo navigation
- Orientation: `CLAUDE.md`; module map: `ARCHITECTURE.md`
- Remediation history + roadmap: `docs/REMEDIATION_PLAN.md`
- Specs: `specs/` (historical pre-remediation design docs — see the banner in each)
- Proposals: `proposals/` (feature proposals)

## When changing code
- The single source of truth is SQLite (`data/tunr.db`); the legacy pickle/numpy
  store is retired. Don't reintroduce it.
- Run `make ci` before finishing (a `Stop` hook also runs the tests).
- Add short doc updates in `README.md`/`CLAUDE.md` if behavior changes.
- Prefer small, testable changes; add/extend tests in `tests/` (keep them offline).
