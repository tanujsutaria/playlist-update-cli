# Codex Agent Notes (playlist-update-cli)

## Project summary
- CLI tool for Spotify playlist management with rotation, similarity, and history tracking.
- Entry point: `src/main.py` (argparse-based).
- Core modules: `src/db_manager.py`, `src/spotify_manager.py`, `src/rotation_manager.py`, `src/models.py`.
- State lives in `data/` (embeddings, history) and `.spotify_cache/` (tokens).

## Safety + state
- Avoid modifying `data/`, `backups/`, or `.spotify_cache/` unless explicitly asked.
- Do not run Spotify API calls or OAuth flows unless the user requests it.
- Prefer read-only inspection when exploring history or embeddings.

## Common commands
- Install deps: `uv pip sync` or `pip install -e .`
- Run CLI: `python src/main.py <command>`
- Tests: `pytest`

## Repo navigation
- Specs: `specs/` (design + flows + state)
- Proposals: `proposals/` (refactor plan)
- Prompts: `prompts/` (task templates)

## When changing code
- Keep backward compatibility with existing pickle + numpy data formats.
- Add short doc updates in `README.md` if behavior changes.
- Prefer small, testable changes; add/extend tests in `src/test_*.py` when possible.
