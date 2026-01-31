# Codex Agent Notes (playlist-update-cli)

## Project summary
- CLI tool for Spotify playlist management with rotation, similarity, and history tracking.
- Entry point: `src/main.py` launches the interactive Textual UI (also exposed as `tunr`).
- Core modules: `src/db_manager.py`, `src/spotify_manager.py`, `src/rotation_manager.py`, `src/models.py`.
- State lives in `data/` (embeddings, history) and `.spotify_cache/` (tokens).

## Safety + state
- Avoid modifying `data/`, `backups/`, or `.spotify_cache/` unless explicitly asked.
- Do not run Spotify API calls or OAuth flows unless the user requests it.
- Prefer read-only inspection when exploring history or embeddings.

## Common commands
- Install deps: `uv pip sync` or `pip install -e .`
- Run app: `tunr` or `python src/main.py`
- Tests: `pytest`

## Repo navigation
- Specs: `specs/` (design + flows + state)
- Proposals: `proposals/` (refactor plan)
- AI docs: `ai_docs/` (assistant notes)

## When changing code
- Keep backward compatibility with existing pickle + numpy data formats.
- Add short doc updates in `README.md` if behavior changes.
- Prefer small, testable changes; add/extend tests in `tests/` when possible.
