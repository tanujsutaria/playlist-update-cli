# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo. This is the canonical
orientation file; see `AGENTS.md` for safety/state notes and
`docs/REMEDIATION_PLAN.md` for the remediation history and roadmap.

## What this is

**tunr** — an interactive CLI for Spotify playlist management: smart rotation,
AI-assisted deep song search, analytics, backups. It runs as a full-screen
Textual TUI where you type slash-style commands (`/update`, `/search`, `/rotate`,
`/ingest`, `/backup`, …).

## Commands

Use the **Makefile** — CI runs the exact same targets, so local ≡ CI.

| Task | Command |
|---|---|
| Install deps + hooks | `make install` (uses `uv`; runs `pre-commit install`) |
| Run the app | `tunr` (needs a TTY — it's a Textual UI) |
| Tests | `make test` (≈5s, offline) |
| Lint | `make lint` (ruff) |
| Format | `make format` / check-only `make format-check` |
| Type-check | `make typecheck` (mypy) |
| Full gate (what CI runs) | `make ci` |
| Coverage | `make cov` (gate: `COV_MIN` in the Makefile) |

- **Run tests via the venv**, e.g. `make test` or `.venv/bin/python -m pytest`.
  Do **not** rely on a bare `python -m pytest` from outside the venv.
- Manual install without make: `uv pip install -e '.[dev]'`.

## Architecture (current, post-consolidation)

**Package layout — `src/` is the source root, NOT a package named `src`.**
Top-level modules import each other bare (`from models import Song`,
`from web_search import …`); sub-packages are `storage` and `nextgen`
(`from storage.repos import Repositories`). There is **no** `sys.path` hack and
**no** `import src.x` — every module has a single import identity. `pyproject.toml`
declares the top-level modules in `[tool.setuptools] py-modules` and finds the
sub-packages.

**Single system of record: SQLite at `data/tunr.db` (schema v7).** Managed by
`storage/` (`Database`, `migrations.ensure_schema`, `Repositories`, `vectors`).
The legacy pickle/numpy store and `db_manager.py` are **retired** — data was
migrated and re-embedded (768-dim `all-mpnet-base-v2`) via
`scripts/migrate_legacy.py` + `storage/legacy_migrate.py`. Code reads/writes the
store through **`SongStore`** (`src/song_store.py`), an adapter that exposes the
old database interface (`get_all_songs`, `get_song_by_id`, `find_similar_songs`,
`generate_embedding`, …) over the repos + the canonical embedding model.

**Entry & dispatch.** `main.py` holds `PlaylistCLI` (lazy `db`/`spotify`/`repos`/
`search_pipeline` properties) and `dispatch_command(cli, command, args) -> int`,
which looks up a **`_COMMAND_HANDLERS` registry** (name → handler) rather than a
big if/elif ladder. `arg_parse.py` builds the parser; `interactive_app.py` is the
Textual UI and calls `dispatch_command`.

**Search pipeline.** `nextgen/` — `SearchPipeline` orchestrates providers
(`providers.run_providers` → `web_search`), canonicalization, context extraction,
embedding (`embeddings.EmbeddingModel`), and scoring (`nextgen/scoring.py`),
caching results in SQLite.

**LLM wrappers.** `anthropic_web_search_wrapper.py`, `openai_web_search_wrapper.py`,
`openai_web_score_wrapper.py` are standalone subprocess scripts invoked by absolute
path; they share JSON helpers from `src/llm_json.py`.

See `ARCHITECTURE.md` for the full module map.

## Conventions & gotchas

- **Python floor is 3.9.** ruff `target-version = "py39"` enforces syntax; new-style
  annotations need `from __future__ import annotations`. (mypy's analysis target is
  3.10 because mypy 2.x dropped 3.9 — runtime support is still 3.9.)
- **Track IDs**: `models.track_id_for(artist, name)` → `"artist|||name"` (lowercased).
  This is the primary key in `tracks` and the `Song.id`.
- **Two scoring configs** (don't confuse): `scoring.PlaylistScoreConfig` (rotation /
  local match scoring) vs `nextgen.scoring.SearchScoreConfig` (search ranking).
- **Tests are offline & fast.** `tests/conftest.py` injects a deterministic stub for
  `sentence_transformers` (the real import is ~17s and downloads a model). Keep new
  tests offline — no network, no real Spotify credentials, use the mock fixtures.
- **mypy is a non-strict baseline.** `pyproject.toml` ignores a shrinking list of
  legacy modules (`main`, `interactive_app`, `web_search`, …). When you finish
  cleaning one, drop it from the ignore list. The core (`models`, `storage.*`, most
  of `nextgen.*`, `config`, `song_store`, `ui`) is fully checked.
- **Secrets**: never read or print `.spotify_cache/**` or `config/.env*`. Tokens are
  gitignored; the cache dir/file are kept `0700`/`0600`.
- **`WEB_SEARCH_*` / `WEB_SCORE_*` env vars execute arbitrary local commands** as
  subprocesses — treat them as trusted-input only.
- **State dirs** (`data/`, `backups/`, `imports/`, `.spotify_cache/`) are gitignored;
  don't modify them unless asked. Snapshots of pre-migration data live under `backups/`.

## Before you finish

Run `make ci` (lint + format-check + types + tests + coverage). A `Stop` hook in
`.claude/settings.json` runs the test suite automatically; if it fails, fix it
before wrapping up. CI (`.github/workflows/ci.yml`) runs the same gate on
py3.9–3.12.
