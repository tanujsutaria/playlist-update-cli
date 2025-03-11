# Spotify Playlist Manager - Design Document

## System Overview

The Spotify Playlist Manager is a command-line tool that provides intelligent management of Spotify playlists. It enables users to import songs, update playlists with smart rotation algorithms, and perform various playlist management operations.

## Architecture

The system follows a modular architecture with clear separation of concerns:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│                 │     │                 │     │                 │
│  Command Line   │────▶│  Core Managers  │────▶│  Spotify API    │
│  Interface      │     │                 │     │  Integration    │
│                 │     │                 │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │                      │                       │
         │                      │                       │
         ▼                      ▼                       ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│                 │     │                 │     │                 │
│  Local Database │◀───▶│  Song Rotation  │     │  Authentication │
│  & Embeddings   │     │  Algorithms     │     │  & Token Mgmt   │
│                 │     │                 │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## Key Components

### 1. Command Line Interface (`main.py`, `arg_parse.py`)
- Parses user commands and arguments
- Delegates to appropriate manager classes
- Provides user feedback and output formatting

### 2. Database Manager (`db_manager.py`)
- Manages persistent storage of songs and metadata
- Handles song embeddings for similarity search
- Provides CRUD operations for the song database

### 3. Spotify Manager (`spotify_manager.py`)
- Handles authentication with Spotify API
- Manages playlist operations (create, update, delete)
- Searches for songs and retrieves track information

### 4. Rotation Manager (`rotation_manager.py`)
- Implements intelligent song rotation algorithms
- Tracks playlist history and generation statistics
- Selects songs based on various criteria (freshness, similarity)

### 5. Models (`models.py`)
- Defines data structures for songs, playlists, and statistics
- Provides type hints and data validation

## Data Flow

### Song Import Flow
```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│          │     │          │     │          │     │          │
│  Parse   │────▶│ Validate │────▶│ Generate │────▶│  Store   │
│  Input   │     │  Songs   │     │ Embedding│     │ in DB    │
│          │     │          │     │          │     │          │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
```

### Playlist Update Flow
```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│          │     │          │     │          │     │          │
│  Select  │────▶│  Search  │────▶│  Update  │────▶│  Update  │
│  Songs   │     │ Spotify  │     │ Playlist │     │ History  │
│          │     │          │     │          │     │          │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
```

## Song Selection Algorithm

The system uses a multi-tier approach to select songs for playlist rotation:

1. **Priority Tier 1**: Songs that have never been used
2. **Priority Tier 2**: Songs not used in the last N days (configurable)
3. **Priority Tier 3**: Similarity-based selection using embeddings
4. **Fallback**: Random selection from remaining pool

## Persistence Strategy

Data is persisted in several locations:

- **Song Database**: Stored as pickle files in `data/embeddings/`
- **Embeddings**: Stored as NumPy arrays in `data/embeddings/`
- **Playlist History**: Stored as pickle files in `data/history/`
- **Spotify Authentication**: Cached in `.spotify_cache/`
- **Dependencies**: Managed via `pyproject.toml`

## Backup and Restore

The system provides backup and restore functionality:

- **Backup**: Creates timestamped or named copies of the data directory
- **Restore**: Replaces current data with a selected backup

## Error Handling

The system implements comprehensive error handling:

- Spotify API errors (authentication, rate limiting, etc.)
- File I/O errors
- Song validation errors
- Database consistency errors

## Future Enhancements

Potential areas for improvement:

1. **Web Interface**: Add a web-based UI for easier interaction
2. **Advanced Analytics**: More detailed statistics and visualizations
3. **Multi-User Support**: Allow multiple users with separate configurations
4. **Improved Embeddings**: Use more sophisticated embedding models
5. **Playlist Scheduling**: Automated playlist updates on a schedule
6. **Package Distribution**: Publish as installable package using pyproject.toml
