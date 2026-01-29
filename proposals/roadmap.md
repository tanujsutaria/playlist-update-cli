# Playlist Update CLI - Feature Roadmap

## Context
This roadmap builds on the current CLI architecture (argparse entry in `src/main.py`, Spotify + rotation + embeddings) and the existing refactor proposal in `proposals/playlist_update_cli_refactor_proposal.md`.

## Now (1-2 weeks)
- **Codex-first setup**: keep `AGENTS.md` current; add `config/.env.example` + setup instructions.
- **CLI usability**: add `--dry-run` for `update` (show selected tracks without writing to Spotify).
- **Stability**: add retries/backoff for Spotify API calls and clearer error messages for auth failures.
- **Testing**: introduce Spotify API mocks/fixtures to make tests deterministic.
- **Data safety**: add `backup --verify` (checksum) and `restore --list` commands.

## Next (1-2 months)
- **Refactor to package**: follow the proposed module layout (cli/core/services/models).
- **Click-based CLI**: improved help, colorized output, tab completion.
- **Configuration**: typed settings loader (env + config file), with validation errors that point to the exact missing keys.
- **Playlist planning**: `plan` command to preview future rotations with similarity/freshness constraints.
- **History integrity**: repair or reconcile history when songs are removed from the database.

## Later (2-6 months)
- **Scheduling**: cron-like scheduled updates with a local task runner.
- **Multi-profile support**: multiple Spotify accounts + separate data directories.
- **Richer embeddings**: use Spotify audio features or external embedding models; allow strategy selection.
- **Analytics**: playlist rotation dashboards (coverage, repeat rate, freshness trends).
- **Storage upgrade**: optional SQLite backend (still supporting pickle for migration).

## Stretch ideas
- **Web UI**: lightweight local web dashboard.
- **Collaborative playlists**: shared rotation rules across users.
- **Smart filtering**: mood/genre constraints, BPM ranges, or explicit/clean toggles.

## Notes
- Preserve existing pickle + numpy formats until migration tooling exists.
- Keep Spotify API calls opt-in to avoid unintended playlist modifications.
