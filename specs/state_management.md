# Spotify Playlist Manager - State Management

## Overview

The Spotify Playlist Manager maintains several types of state across sessions:

1. **Song Database**: Persistent storage of all songs and their metadata
2. **Playlist History**: Record of which songs have been used in each playlist generation
3. **Spotify Authentication**: OAuth tokens for Spotify API access
4. **Embeddings**: Vector representations of songs for similarity search

## Song Database State

### Storage Format

Songs are stored in a Python dictionary serialized with pickle:

```python
{
    "artist|||song_name": Song(
        id="artist|||song_name",
        name="song_name",
        artist="artist",
        embedding=np.array(...),
        spotify_uri="spotify:track:...",
        first_added=datetime(...)
    ),
    # More songs...
}
```

### State Transitions

1. **Add Song**:
   - Generate embedding
   - Add to songs dictionary
   - Update embeddings array
   - Save state to disk

2. **Remove Song**:
   - Remove from songs dictionary
   - Remove corresponding embedding
   - Save state to disk

3. **Update Song**:
   - Modify song attributes
   - Save state to disk

## Playlist History State

### Storage Format

Each playlist's history is stored in a separate pickle file:

```python
PlaylistHistory(
    playlist_id="spotify:playlist:...",
    name="playlist_name",
    generations=[
        ["song_id_1", "song_id_2", ...],  # Generation 1
        ["song_id_3", "song_id_4", ...],  # Generation 2
        # More generations...
    ],
    current_generation=2
)
```

### State Transitions

1. **New Generation**:
   - Add list of song IDs to generations list
   - Increment current_generation
   - Save state to disk

2. **Reset History**:
   - Create new PlaylistHistory object
   - Save state to disk

## Spotify Authentication State

### Storage Format

Spotify authentication tokens are stored in a cache file managed by the Spotipy library:

```
.spotify_cache/.spotify_token
```

### State Transitions

1. **Initial Authentication**:
   - User authorizes application
   - Tokens stored in cache

2. **Token Refresh**:
   - Refresh token used to get new access token
   - Updated tokens stored in cache

## Embeddings State

### Storage Format

Embeddings are stored as a NumPy array:

```
embeddings.npy  # Shape: (num_songs, embedding_dim)
```

### State Transitions

1. **Add Embedding**:
   - Generate new embedding vector
   - Append to embeddings array
   - Save to disk

2. **Remove Embedding**:
   - Delete row from embeddings array
   - Save to disk

3. **Rebuild Embeddings**:
   - Regenerate all embeddings
   - Replace embeddings array
   - Save to disk

## State Consistency

The system ensures state consistency through several mechanisms:

1. **Atomic Operations**: Critical state changes are performed atomically
2. **Validation**: State is validated before and after changes
3. **Backup**: Regular state backups
4. **Recovery**: Ability to restore from backups

## State Initialization

When the application starts:

1. Load song database if exists, otherwise create empty
2. Load embeddings if exists, otherwise create empty
3. Initialize Spotify authentication from cache or prompt user
4. Load playlist histories as needed

## State Synchronization

The system maintains synchronization between different state components:

1. **Songs and Embeddings**: Each song has a corresponding embedding vector
2. **Playlist History and Songs**: Song IDs in history must exist in database
3. **Spotify URIs and Songs**: Songs should have valid Spotify URIs

## Backup and Restore

The backup and restore functionality preserves all state:

1. **Backup**: Creates a copy of the entire data directory
2. **Restore**: Replaces current data with a backup copy
