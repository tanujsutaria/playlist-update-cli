# Spotify Playlist Manager CLI - v0 Specification

## Overview

The Spotify Playlist Manager CLI is a command-line tool for managing Spotify playlists with intelligent song rotation and similarity-based recommendations. This document outlines the design and implementation details for version 0 of the CLI.

## Command Structure

The CLI follows a command-based structure with the following main commands:

```
python src/main.py <command> [options]
```

### Core Commands

| Command | Description | Options |
|---------|-------------|---------|
| `import` | Import songs from a file | `file`: Path to input file |
| `update` | Update a playlist with new songs | `playlist`: Playlist name<br>`--count`: Number of songs<br>`--fresh-days`: Days threshold |
| `stats` | Show database and playlist statistics | `--playlist`: Optional playlist name |
| `view` | View current playlist contents | `playlist`: Playlist name |
| `sync` | Sync database to a playlist | `playlist`: Playlist name |
| `extract` | Extract playlist contents to a file | `playlist`: Playlist name<br>`--output`: Output file path |
| `clean` | Clean database | `--dry-run`: Show without removing |
| `backup` | Backup data directory | `backup_name`: Optional name |
| `restore` | Restore from backup | `backup_name`: Backup name |

## Component Architecture

The system is built with a modular architecture consisting of these key components:

1. **PlaylistCLI**: Main CLI interface that processes commands
2. **DatabaseManager**: Manages song database and embeddings
3. **SpotifyManager**: Handles Spotify API interactions
4. **RotationManager**: Implements playlist rotation algorithms

## Workflow Examples

### Song Import Workflow

1. User runs: `python src/main.py import songs.csv`
2. CLI parses the file and validates each song:
   - Checks if song exists in Spotify
   - Verifies artist has less than 1M followers
3. Valid songs are added to the database with embeddings
4. Statistics are displayed showing import results

### Playlist Update Workflow

1. User runs: `python src/main.py update "My Playlist" --count 10 --fresh-days 30`
2. CLI selects songs using the rotation algorithm:
   - Prioritizes songs never used before
   - Then songs not used in the last 30 days
   - Then similar songs based on embeddings
3. Selected songs are used to update the Spotify playlist
4. Statistics are displayed showing update results

## Data Structures

### Song

```python
@dataclass
class Song:
    id: str                      # Unique identifier (artist|||song_name)
    name: str                    # Song name
    artist: str                  # Artist name
    embedding: Optional[List[float]] = None  # Vector representation
    spotify_uri: Optional[str] = None        # Spotify URI
    first_added: Optional[datetime] = None   # When added to database
```

### PlaylistHistory

```python
@dataclass
class PlaylistHistory:
    playlist_id: str             # Spotify playlist ID
    name: str                    # Playlist name
    generations: List[List[str]] # List of generations (song IDs)
    current_generation: int = 0  # Current generation count
```

## File Format

Songs are imported from CSV or TXT files with the format:

```
song_name,artist_name
Dancing Queen,ABBA
Bohemian Rhapsody,Queen
```

Comments (lines starting with #) and empty lines are ignored.

## Error Handling

The system implements comprehensive error handling:

- Spotify API errors (authentication, rate limiting)
- File I/O errors
- Song validation errors
- Database consistency errors
- Dependency management via pyproject.toml

## Future Enhancements

Planned for future versions:

1. Web interface for easier interaction
2. Advanced analytics and visualizations
3. Multi-user support
4. Improved embedding models
5. Playlist scheduling
6. Distribution as installable package using pyproject.toml
