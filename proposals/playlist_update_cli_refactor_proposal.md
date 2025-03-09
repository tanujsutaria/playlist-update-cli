# Playlist Update CLI Refactoring Proposal

## Overview

This document outlines a proposal to refactor the Playlist Update CLI application to follow more robust software engineering design principles while maintaining all existing functionality. The refactoring will focus on improving code organization, testability, maintainability, and dependency management.

## Key Objectives

1. Implement a more modular architecture with clear separation of concerns
2. Migrate from requirements.txt to pyproject.toml for modern dependency management
3. Improve error handling and logging
4. Enhance testability with proper dependency injection
5. Standardize code style and documentation
6. Maintain backward compatibility with existing data formats

## Proposed Architecture

### 1. Package Structure

```
playlist-update-cli/
├── pyproject.toml           # Project metadata and dependencies
├── README.md                # Project documentation
├── .gitignore               # Git ignore file
├── .env.example             # Example environment variables
├── playlist_update_cli/     # Main package
│   ├── __init__.py          # Package initialization
│   ├── cli/                 # CLI-specific code
│   │   ├── __init__.py
│   │   ├── commands.py      # Command implementations
│   │   └── app.py           # CLI application entry point
│   ├── core/                # Core business logic
│   │   ├── __init__.py
│   │   ├── database.py      # Database operations
│   │   ├── rotation.py      # Playlist rotation logic
│   │   └── similarity.py    # Song similarity calculations
│   ├── models/              # Data models
│   │   ├── __init__.py
│   │   └── entities.py      # Entity classes (Song, Playlist, etc.)
│   ├── services/            # External service integrations
│   │   ├── __init__.py
│   │   └── spotify.py       # Spotify API integration
│   ├── config/              # Configuration management
│   │   ├── __init__.py
│   │   └── settings.py      # Application settings
│   └── utils/               # Utility functions
│       ├── __init__.py
│       ├── logging.py       # Logging utilities
│       └── file_io.py       # File I/O utilities
├── tests/                   # Test suite
│   ├── __init__.py
│   ├── conftest.py          # Test fixtures
│   ├── unit/                # Unit tests
│   │   ├── __init__.py
│   │   ├── test_database.py
│   │   ├── test_rotation.py
│   │   └── test_spotify.py
│   └── integration/         # Integration tests
│       ├── __init__.py
│       └── test_cli.py
└── data/                    # Data directory (gitignored)
    ├── embeddings/
    └── history/
```

### 2. Design Patterns

1. **Repository Pattern** for data access
   - Abstract database operations behind interfaces
   - Allow for different storage backends (file, SQLite, etc.)

2. **Service Pattern** for external integrations
   - Encapsulate Spotify API interactions
   - Make services easily mockable for testing

3. **Command Pattern** for CLI operations
   - Each CLI command implemented as a separate class
   - Common functionality shared through inheritance

4. **Dependency Injection** for better testability
   - Inject dependencies rather than creating them internally
   - Use a simple DI container for managing dependencies

5. **Strategy Pattern** for song selection algorithms
   - Allow different algorithms to be swapped easily
   - Make it easy to add new selection strategies

### 3. Modern Dependency Management

Replace requirements.txt with pyproject.toml:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "playlist-update-cli"
version = "1.0.0"
description = "A CLI tool for managing Spotify playlists with smart rotation"
readme = "README.md"
requires-python = ">=3.8"
license = {text = "MIT"}
authors = [
    {name = "Your Name", email = "your.email@example.com"},
]
dependencies = [
    "spotipy>=2.19.0",
    "numpy>=1.20.0",
    "scikit-learn>=1.0.0",
    "python-dotenv>=0.19.0",
    "click>=8.0.0",
    "tabulate>=0.8.9",
    "tqdm>=4.62.0",
    "pydantic>=1.9.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-cov>=3.0.0",
    "black>=22.1.0",
    "isort>=5.10.0",
    "mypy>=0.931",
    "ruff>=0.0.54",
]

[project.scripts]
playlist-cli = "playlist_update_cli.cli.app:main"

[tool.hatch.build.targets.wheel]
packages = ["playlist_update_cli"]

[tool.black]
line-length = 88
target-version = ["py38"]

[tool.isort]
profile = "black"
line_length = 88

[tool.mypy]
python_version = "3.8"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
```

## Implementation Strategy

### Phase 1: Project Setup and Dependency Migration

1. Create the new project structure
2. Set up pyproject.toml with dependencies
3. Configure development tools (black, isort, mypy, pytest)
4. Create basic package scaffolding

### Phase 2: Core Components Refactoring

1. Refactor models into Pydantic models for validation
2. Implement repository interfaces for data access
3. Refactor database operations to use the repository pattern
4. Create service interfaces for external integrations
5. Implement Spotify service using the service pattern

### Phase 3: Business Logic Refactoring

1. Refactor rotation logic into dedicated classes
2. Implement song selection strategies
3. Create interfaces for embedding generation
4. Refactor similarity calculations

### Phase 4: CLI Refactoring

1. Implement CLI using Click instead of argparse
2. Create command classes for each operation
3. Implement dependency injection for commands
4. Add improved error handling and user feedback

### Phase 5: Testing and Documentation

1. Write unit tests for core components
2. Write integration tests for CLI commands
3. Add comprehensive docstrings
4. Update README and documentation

## Key Improvements

### 1. Code Organization

- **Current**: Monolithic classes with many responsibilities
- **Proposed**: Small, focused classes with single responsibilities
- **Benefit**: Easier to understand, maintain, and test

### 2. Dependency Management

- **Current**: requirements.txt without version pinning
- **Proposed**: pyproject.toml with proper version constraints
- **Benefit**: More reliable builds, better dependency resolution with uv

### 3. Error Handling

- **Current**: Basic error handling with logging
- **Proposed**: Structured error handling with custom exceptions
- **Benefit**: More robust error recovery and better user feedback

### 4. Testability

- **Current**: Limited testing with tightly coupled components
- **Proposed**: Comprehensive testing with mockable dependencies
- **Benefit**: Higher test coverage, more reliable code

### 5. Type Safety

- **Current**: Limited type annotations
- **Proposed**: Comprehensive type annotations with mypy validation
- **Benefit**: Catch errors at development time, better IDE support

## Backward Compatibility

To ensure backward compatibility:

1. Maintain the same data formats for embeddings and history
2. Provide migration utilities for any data format changes
3. Support the same command-line interface with deprecation warnings for changed options
4. Document migration path for users

## CLI Interface Improvements

### Current CLI:

```
python -m src.main update "My Playlist" --count 10 --fresh-days 30
```

### Proposed CLI:

```
playlist-cli update "My Playlist" --count 10 --fresh-days 30
```

Benefits:
- Installable as a system-wide command
- Tab completion support
- Better help documentation
- Colorized output

## Implementation Example: Song Repository

```python
# Current implementation (simplified)
class DatabaseManager:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.songs = self._load_songs()
        
    def _load_songs(self) -> dict:
        songs_path = self.data_dir / "songs.pkl"
        if songs_path.exists():
            with open(songs_path, 'rb') as f:
                return pickle.load(f)
        return {}
        
    def add_song(self, song: Song) -> bool:
        if song.id in self.songs:
            return False
        self.songs[song.id] = song
        self._save_state()
        return True

# Proposed implementation
class SongRepository(Protocol):
    def get_all(self) -> List[Song]:
        ...
    def get_by_id(self, song_id: str) -> Optional[Song]:
        ...
    def add(self, song: Song) -> bool:
        ...
    def remove(self, song_id: str) -> bool:
        ...

class FileSongRepository:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._songs = self._load()
        
    def _load(self) -> Dict[str, Song]:
        songs_path = self.storage_path / "songs.pkl"
        if songs_path.exists():
            with open(songs_path, 'rb') as f:
                return pickle.load(f)
        return {}
        
    def _save(self) -> None:
        with open(self.storage_path / "songs.pkl", 'wb') as f:
            pickle.dump(self._songs, f)
            
    def get_all(self) -> List[Song]:
        return list(self._songs.values())
        
    def get_by_id(self, song_id: str) -> Optional[Song]:
        return self._songs.get(song_id)
        
    def add(self, song: Song) -> bool:
        if song.id in self._songs:
            return False
        self._songs[song.id] = song
        self._save()
        return True
        
    def remove(self, song_id: str) -> bool:
        if song_id not in self._songs:
            return False
        del self._songs[song_id]
        self._save()
        return True
```

## Implementation Example: CLI Command

```python
# Current implementation (simplified)
def main():
    cli = PlaylistCLI()
    command, args = parse_args()
    
    if command == 'update':
        cli.update_playlist(args.playlist, args.count, args.fresh_days)

# Proposed implementation with Click
import click

@click.group()
def cli():
    """Playlist Update CLI - Manage your Spotify playlists with smart rotation."""
    pass

@cli.command()
@click.argument('playlist')
@click.option('--count', default=10, help='Number of songs to include')
@click.option('--fresh-days', default=30, help='Prioritize songs not used in this many days')
def update(playlist, count, fresh_days):
    """Update a playlist with new songs."""
    from playlist_update_cli.cli.commands import UpdateCommand
    from playlist_update_cli.core.database import get_song_repository
    from playlist_update_cli.services.spotify import get_spotify_service
    from playlist_update_cli.core.rotation import get_rotation_manager
    
    song_repo = get_song_repository()
    spotify_service = get_spotify_service()
    rotation_manager = get_rotation_manager(playlist, song_repo, spotify_service)
    
    cmd = UpdateCommand(rotation_manager)
    result = cmd.execute(count=count, fresh_days=fresh_days)
    
    if result.success:
        click.echo(click.style(f"Successfully updated playlist '{playlist}'", fg='green'))
        click.echo(f"Added {len(result.songs)} songs")
    else:
        click.echo(click.style(f"Failed to update playlist: {result.error}", fg='red'))

def main():
    cli()

if __name__ == '__main__':
    main()
```

## Conclusion

This refactoring proposal aims to transform the Playlist Update CLI into a more robust, maintainable, and testable application while preserving all existing functionality. By adopting modern Python practices, design patterns, and dependency management, the application will be easier to extend, maintain, and contribute to.

The migration to pyproject.toml will enable the use of modern tools like uv for dependency management, making the development workflow more efficient and reliable.

## Next Steps

1. Create a detailed implementation plan with milestones
2. Set up the new project structure and dependency management
3. Begin incremental refactoring of core components
4. Implement comprehensive tests
5. Document the new architecture and usage 