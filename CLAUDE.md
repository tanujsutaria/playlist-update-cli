# CLAUDE.md - AI Assistant Guidelines

## Project Overview

**Spotify Playlist Manager CLI** - A Python command-line tool for intelligent management of Spotify playlists with smart song rotation, AI-powered similarity recommendations, and comprehensive playlist tracking.

### Key Capabilities
- Import songs from CSV/TXT files into a local database
- Automatically rotate songs in playlists using smart selection algorithms
- Find similar songs using TF-IDF embeddings
- Track playlist history and rotation progress
- Sync entire song database to playlists
- Backup and restore playlist data

---

## Project Structure

```
playlist-update-cli/
├── src/                          # Core application code
│   ├── main.py                   # CLI entry point and command handlers
│   ├── arg_parse.py              # Command-line argument parsing
│   ├── models.py                 # Data models (Song, PlaylistHistory, RotationStats)
│   ├── db_manager.py             # Database and embeddings management
│   ├── spotify_manager.py        # Spotify API integration
│   ├── rotation_manager.py       # Playlist rotation logic
│   ├── setup.py                  # Initial setup and verification
│   └── test_*.py                 # Unit and integration tests
├── config/                       # Configuration
│   └── .env                      # Environment variables (Spotify credentials)
├── data/                         # Persistent data (created at runtime)
│   ├── embeddings/               # songs.pkl and embeddings.npy
│   ├── history/                  # Playlist rotation histories
│   └── state/                    # Additional state files
├── backups/                      # Backup copies of data/ directory
├── specs/                        # Design and architecture documentation
├── ai_docs/                      # AI assistant documentation
├── prompts/                      # Prompt templates for AI tools
├── proposals/                    # Feature proposals
├── README.md                     # User-facing documentation
├── pyproject.toml                # Project metadata and dependencies
└── uv.lock                       # Dependency lock file
```

---

## Quick Reference

### Running Commands
```bash
# Entry point
python src/main.py <command> [options]

# Common commands
python src/main.py import songs.csv                          # Import songs
python src/main.py update "My Mix" --count 10                # Update playlist
python src/main.py stats                                     # Show statistics
python src/main.py view "My Mix"                             # View playlist
python src/main.py sync "My Mix"                             # Sync database to playlist
python src/main.py list-rotations "My Mix" --generations 5   # View rotation history
python src/main.py restore-previous-rotation "My Mix" -3     # Restore older rotation
python src/main.py backup my_backup                          # Create backup
```

### Running Tests
```bash
pytest src/test_*.py        # Run all tests
pytest src/test_rotation.py # Run specific test file
```

### Installation
```bash
uv pip sync           # Using uv (recommended)
pip install -e .      # Or using pip
python src/setup.py   # Initial setup
```

---

## Architecture

### Core Components

| File | Component | Responsibility |
|------|-----------|----------------|
| `src/db_manager.py` | `DatabaseManager` | Song database and TF-IDF embeddings management |
| `src/spotify_manager.py` | `SpotifyManager` | Spotify API interactions and OAuth2 |
| `src/rotation_manager.py` | `RotationManager` | Smart song rotation and history tracking |
| `src/main.py` | `PlaylistCLI` | Command routing and user interface |
| `src/models.py` | `Song`, `PlaylistHistory`, `RotationStats` | Data models |
| `src/arg_parse.py` | `parse_args()` | Command-line argument parsing |

### Data Flow
```
User Commands (CLI)
        ↓
PlaylistCLI (Command Router)
        ↓
Manager Layer (DatabaseManager, SpotifyManager, RotationManager)
        ↓
Data Layer (pickle files, numpy arrays, Spotify API)
```

---

## Code Conventions

### Naming
- **Classes:** PascalCase (`DatabaseManager`, `SpotifyManager`)
- **Methods/Variables:** snake_case (`add_song`, `playlist_name`)
- **Song ID Format:** `"artist_name|||song_name"` (lowercase, three pipes)

### Key Patterns

**Lazy Initialization:**
```python
@property
def db(self) -> DatabaseManager:
    if self._db is None:
        self._db = DatabaseManager()
    return self._db
```

**Error Handling:**
- Use try-except with logging
- Provide user-friendly error messages
- Use graceful degradation with fallbacks

**Type Hints:**
- Use `Optional`, `List`, `Dict` annotations throughout
- All public methods should have type hints

**Logging:**
```python
logger = logging.getLogger(__name__)
# Format: %(asctime)s - %(levelname)s - %(message)s
```

---

## Data Models

### Song
```python
@dataclass
class Song:
    id: str                              # "artist|||song_name"
    name: str
    artist: str
    embedding: Optional[List[float]]     # TF-IDF vector (384 dims)
    spotify_uri: Optional[str]           # spotify:track:...
    first_added: Optional[datetime]
```

### PlaylistHistory
```python
@dataclass
class PlaylistHistory:
    playlist_id: str
    name: str
    generations: List[List[str]]         # List of song ID lists
    current_generation: int = 0
```

---

## Key Algorithms

### Song Selection (4-Tier Priority)
1. **Tier 1:** Never-used songs (highest priority)
2. **Tier 2:** Songs not used in last N days
3. **Tier 3:** Similar songs (cosine similarity >= 0.7)
4. **Fallback:** Random selection from remaining

### Spotify Song Search
1. Try exact artist + track search
2. General search with both terms
3. Fuzzy matching with SequenceMatcher (80%+ threshold)
4. Artist matching weighted 60%, name matching 40%

---

## State & Persistence

| State | Location | Format |
|-------|----------|--------|
| Song Database | `data/embeddings/songs.pkl` | Pickle dictionary |
| Embeddings | `data/embeddings/embeddings.npy` | NumPy array (N × 384) |
| Playlist History | `data/history/{playlist}.pkl` | Pickle PlaylistHistory |
| Spotify Token | `.spotify_cache/.spotify_token` | Spotipy cache |
| Configuration | `config/.env` | Environment variables |

### Required Environment Variables
```
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://localhost:8888/callback
```

---

## Important Implementation Details

### Embedding Dimensions
- Fixed at 384 dimensions
- Must remain consistent across all songs
- Uses TF-IDF vectorization with English stopwords

### Spotify API Limits
- Batch operations: 50 items per call
- Rate limiting handled by Spotipy

### Playlist Refresh Strategy
1. Delete existing playlist completely
2. Create new playlist with same name
3. Add all songs in single operation
4. Record generation in history

### Artist Filter
- Songs with artists >= 1M followers are rejected during import
- Ensures focus on lesser-known music

---

## Common Tasks for AI Assistants

### Adding a New Command
1. Add argument parsing in `src/arg_parse.py`
2. Implement handler method in `PlaylistCLI` class in `src/main.py`
3. Add command routing in the main execution block
4. Update README.md with command documentation

### Modifying Song Selection Logic
- Primary location: `src/rotation_manager.py` → `select_songs_for_today()`
- Consider impact on 4-tier selection algorithm
- Update related tests in `src/test_rotation.py`

### Working with Embeddings
- `DatabaseManager.generate_embedding()` creates TF-IDF vectors
- Embeddings stored separately from songs for efficiency
- Always maintain dimension consistency (384)

### Adding New Data Fields to Song
1. Update `Song` dataclass in `src/models.py`
2. Handle serialization in `DatabaseManager`
3. Consider migration strategy for existing data

---

## Testing Guidelines

- Test files located in `src/test_*.py`
- Use pytest for running tests
- Key test files:
  - `test_spotify.py` - Spotify integration
  - `test_rotation.py` - Rotation algorithm
  - `test_playlist_update.py` - Update workflow
  - `test_delete_recreate.py` - Playlist recreation

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `spotipy` | Spotify Web API client |
| `python-dotenv` | Environment variable management |
| `numpy` | Numerical computing & embeddings |
| `scikit-learn` | TF-IDF vectorization |
| `tabulate` | ASCII table formatting |
| `tqdm` | Progress bars |
| `matplotlib` | Data visualization |
| `click` | CLI framework |
| `pytest` | Testing framework |

---

## Common Pitfalls

1. **Environment Setup:** Must have `config/.env` with valid Spotify credentials
2. **Empty Database:** Import songs before running playlist operations
3. **Embedding Consistency:** Keep 384-dimension vectors across all songs
4. **Song ID Format:** Always lowercase with `|||` delimiter
5. **Spotify URI:** Required for playlist operations - stored with songs
6. **Batch Limits:** Spotify playlist operations limited to 50 items per call

---

## Documentation References

- **User Guide:** `README.md`
- **Architecture:** `specs/design_document.md`
- **Components:** `specs/component_details.md`
- **State Management:** `specs/state_management.md`
- **Flow Diagrams:** `specs/flow_diagrams.md`
