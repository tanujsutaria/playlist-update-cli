# Spotify Playlist Manager (tunr)

Interactive CLI for Spotify playlist management with rotation, similarity, history tracking, and deep search.

## Features
- Full-screen Textual UI with slash commands
- Import songs into a local database
- Rotate playlists using smart selection and similarity scoring
- Deep search for new songs via Claude/Codex and validate on Spotify
- Obscurity and similarity validation for search results
- Playlist history, stats, backups, and restores

## Setup

1. Create a Spotify Developer app at https://developer.spotify.com/dashboard

2. Create `config/.env` with required keys:
   ```
   SPOTIFY_CLIENT_ID=your_client_id
   SPOTIFY_CLIENT_SECRET=your_client_secret
   SPOTIFY_REDIRECT_URI=your_redirect_uri
   ```
   Optional for /search:
   ```
   ANTHROPIC_API_KEY=your_anthropic_key
   OPENAI_API_KEY=your_openai_key
   ```

3. Install dependencies:
   ```bash
   # Using uv (recommended)
   uv pip sync

   # Or using pip
   pip install -e .
   ```

4. Install the `tunr` command:
   ```bash
   uv pip install -e .
   ```

5. Launch the app:
   ```bash
   tunr
   ```
   You can also run `python src/main.py` with no arguments.

## First-time setup
- On launch, `tunr` auto-detects keys in your environment.
- If any Spotify keys are missing, the app enters setup mode.
- Use `/setup` to see instructions and `/env` to confirm detected keys.
- `/help` is available on demand and is not shown automatically.

## Usage

### Output styling
The UI uses Rich for colorized tables and headers. To disable color output, set `NO_COLOR=1` in your shell.

### Search (deep web search)
```bash
/search "late night jazz with soft vocals"
```
After results are shown, confirm whether to add them to the database and/or create a playlist.

To broaden the last search (expanded source policy):
```bash
/expand
```

### Playlist operations
```bash
/update "My Playlist" --count 10 --fresh-days 30
/update "My Playlist" --score-strategy web --query "late night jazz"
/update "My Playlist" --score-strategy hybrid --query "uplifting synth pop"
/view "My Playlist"
/stats --playlist "My Playlist"
/sync "My Playlist"
/extract "My Playlist" --output songs.csv
/plan "My Playlist" --count 10 --fresh-days 30 --generations 3
/diff "My Playlist" --count 10 --fresh-days 30
```

### Import songs
```bash
/import songs.csv
/import songs.txt
```

### Backup and restore
```bash
/backup
/backup my_backup_name
/restore my_backup_name
/list-backups
```

### Rotation history
```bash
/restore-previous-rotation "My Playlist" -1
/restore-previous-rotation "My Playlist" -5
/list-rotations "My Playlist" --generations 5
/list-rotations "My Playlist" -g all
```

### Auth and maintenance
```bash
/auth-status
/auth-refresh
/clean
/clean --dry-run
```

### Help and exit
```bash
/help
/debug
/quit
```

## Input file format
Songs should be in a text file (.txt or .csv) with the following format:
```
song_name,artist_name
Dancing Queen,ABBA
Bohemian Rhapsody,Queen
```

Comments and empty lines are ignored:
```
# My favorite songs
Dancing Queen,ABBA

# Rock section
Bohemian Rhapsody,Queen
```

## Deep search providers
The `/search` command uses Claude/Codex CLIs if available. It checks, in order:
- `WEB_SEARCH_CLAUDE_CMD` / `WEB_SEARCH_CODEX_CMD`
- `WEB_SCORE_CLAUDE_CMD` / `WEB_SCORE_CODEX_CMD`
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (uses `claude` and an OpenAI API wrapper)

You can also set a generic command for search via `WEB_SEARCH_CMD`.
If your CLI needs extra flags (or doesn't support `--json`), set the full command explicitly
with `WEB_SEARCH_CLAUDE_CMD` / `WEB_SEARCH_CODEX_CMD`.

OpenAI API wrapper (recommended for Codex search):
```bash
export WEB_SEARCH_CODEX_CMD="python -m src.openai_web_search_wrapper"
export WEB_SEARCH_MODEL="gpt-4o"
export WEB_SEARCH_TOOL_CHOICE="required"  # optional: auto|required|none
export WEB_SEARCH_TOOL="web_search"        # optional: web_search|web_search_preview
```

## Validation rules for /search
- If your query includes a monthly listeners constraint (for example, "under 50k monthly listeners"),
  results must include a `monthly_listeners` metric or they will be skipped.
- Set `OBSCURITY_VALIDATION_MODE=followers` to approximate monthly listeners using Spotify follower counts.
- If your query implies similarity (for example, "like Royel Otis"), the workflow expects a `similarity` metric.
  Control the minimum similarity with `SEARCH_SIMILARITY_MIN` (default: 0.55).
- Audio similarity validation uses Spotify audio features as an additional filter when similarity is requested.
  Control the minimum with `SEARCH_AUDIO_SIMILARITY_MIN` and the mode with `SEARCH_AUDIO_SIMILARITY_MODE`
  (`strict` or `soft`).

## Match scoring (web + hybrid)
The web and hybrid strategies for `/update`, `/plan`, and `/diff` can call external Claude/Codex commands
for scoring candidates. Provide one or more commands via environment variables:
```bash
export WEB_SCORE_CMD="path/to/your-web-score-wrapper"
export WEB_SCORE_CLAUDE_CMD="claude"
export WEB_SCORE_CODEX_CMD="codex exec --search -"
```
Each command should read JSON from stdin and write JSON to stdout with a `scores` object mapping song IDs
(`artist|||song`) to a 0-1 relevance score.
If your CLI supports JSON flags, add them here (for example, `claude --json`).

OpenAI API wrapper (recommended for scoring):
```bash
export WEB_SCORE_CODEX_CMD="python -m src.openai_web_score_wrapper"
export WEB_SCORE_MODEL="gpt-4o"
export WEB_SCORE_TOOL_CHOICE="required"  # optional: auto|required|none
export WEB_SCORE_TOOL="web_search"       # optional: web_search|web_search_preview
```

If no web command is configured, the app falls back to local scoring.

## Notes
- The classic CLI entrypoint has been removed. Use `tunr` (interactive UI) instead.
