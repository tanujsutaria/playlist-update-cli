# Spotify Playlist Manager - Component Details

## 1. Database Manager

The `DatabaseManager` class is responsible for:

- **Song Storage**: Persists songs and their metadata
- **Embedding Generation**: Creates vector representations of songs
- **Similarity Search**: Finds similar songs using vector similarity

### Key Methods:

| Method | Purpose |
|--------|---------|
| `add_song()` | Adds a new song to the database |
| `get_all_songs()` | Retrieves all songs from the database |
| `find_similar_songs()` | Finds songs similar to a given song |
| `generate_embedding()` | Creates a vector representation of a song |
| `remove_song()` | Removes a song from the database |

### Embedding Generation:

The system uses TF-IDF (Term Frequency-Inverse Document Frequency) to generate embeddings:

1. Combines song name and artist into a text string
2. Applies TF-IDF vectorization with English stopwords removed
3. Ensures consistent vector dimensions across the database
4. Handles edge cases like new databases or dimension mismatches

## 2. Spotify Manager

The `SpotifyManager` class handles all interactions with the Spotify API:

- **Authentication**: Manages OAuth flow and token refreshing
- **Playlist Operations**: Creates, updates, and deletes playlists
- **Song Search**: Finds songs on Spotify and retrieves URIs

### Key Methods:

| Method | Purpose |
|--------|---------|
| `search_song()` | Searches for a song on Spotify |
| `refresh_playlist()` | Recreates a playlist with new songs |
| `get_playlist_tracks()` | Retrieves tracks from a playlist |
| `append_to_playlist()` | Adds songs to an existing playlist |
| `remove_from_playlist()` | Removes songs from a playlist |

### Song Search Algorithm:

The song search uses a multi-step approach:

1. First tries exact artist search with track name
2. Falls back to general search with both terms
3. Uses fuzzy matching with similarity scores
4. Applies special handling for remixes and features
5. Returns the best match above a confidence threshold

## 3. Rotation Manager

The `RotationManager` class implements intelligent playlist rotation:

- **History Tracking**: Records which songs have been used
- **Song Selection**: Chooses songs based on rotation strategy
- **Statistics**: Provides insights into rotation progress

### Key Methods:

| Method | Purpose |
|--------|---------|
| `select_songs_for_today()` | Selects songs for playlist update |
| `update_playlist()` | Updates a playlist with selected songs |
| `get_rotation_stats()` | Retrieves statistics about rotation |
| `get_recent_generations()` | Gets recently used song sets |

### Song Selection Algorithm:

The selection algorithm uses a tiered approach:

1. **Tier 1**: Songs that have never been used
2. **Tier 2**: Songs not used in the last N days
3. **Tier 3**: Songs similar to recently used songs
4. **Fallback**: Random selection from remaining pool

## 4. Command Line Interface

The CLI is implemented through the `PlaylistCLI` class:

- **Command Parsing**: Interprets user commands
- **Operation Execution**: Delegates to appropriate managers
- **Output Formatting**: Presents results in a readable format

### Key Commands:

| Command | Purpose |
|---------|---------|
| `import` | Import songs from a file |
| `update` | Update a playlist with new songs |
| `stats` | Show database and playlist statistics |
| `view` | View current playlist contents |
| `sync` | Sync database to a playlist |
| `extract` | Extract playlist contents to a file |
| `clean` | Remove invalid songs from database |
| `backup` | Create a backup of the data directory |
| `restore` | Restore from a backup |

## 5. Data Persistence

The system uses several persistence mechanisms:

- **Song Database**: Python pickle files
- **Embeddings**: NumPy array files
- **Playlist History**: Python pickle files
- **Spotify Tokens**: Cache files managed by Spotipy

### File Structure:

```
data/
├── embeddings/
│   ├── songs.pkl       # Song database
│   └── embeddings.npy  # Song embeddings
├── history/
│   └── playlist_name.pkl  # Playlist history
└── state/
    └── ...             # Additional state files
```

## 6. Error Handling

The system implements comprehensive error handling:

- **Logging**: Detailed logging at different levels
- **Graceful Degradation**: Fallbacks for common failures
- **User Feedback**: Clear error messages for users
- **Recovery**: Automatic recovery from transient errors

### Common Error Scenarios:

1. **Spotify API Failures**: Authentication issues, rate limiting
2. **File I/O Errors**: Missing or corrupt files
3. **Song Validation**: Songs not found on Spotify
4. **Database Consistency**: Embedding dimension mismatches
