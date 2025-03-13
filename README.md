# Spotify Playlist Manager

A Python script to manage Spotify playlists through the command line, featuring intelligent song rotation and similarity-based recommendations.

## Features
- Import songs from CSV files into local database
- Automatically rotate songs in playlists using smart selection
- Find similar songs using AI-powered embeddings
- View detailed playlist and rotation statistics
- Sync entire song database to a playlist
- Track playlist history and rotation progress
- Backup and restore your `data/` directory

## Setup


1. Create a Spotify Developer account and register your application at https://developer.spotify.com/dashboard

2. Set up environment variables in `config/.env`:
   ```
   SPOTIFY_CLIENT_ID=your_client_id
   SPOTIFY_CLIENT_SECRET=your_client_secret
   SPOTIFY_REDIRECT_URI=your_redirect_uri
   ```

3. Install dependencies:
   ```bash
   # Using uv (recommended)
   uv pip sync
   
   # Or using pip
   pip install -e .
   ```

4. Run initial setup:
   ```bash
   python src/setup.py
   ```

## Usage

### Import Songs
Import songs from a file (supports both .txt and .csv formats):
```bash
python src/main.py import songs.csv
# or
python src/main.py import songs.txt
```

### Backup and Restore

Quickly backup the existing `data/` folder to a `backups/` folder at the same level as `src/`:

```bash
python src/main.py backup
# Optionally specify a name
python src/main.py backup my_backup_name
```

To restore from a backup:
```bash
python src/main.py restore my_backup_name
```

## Input File Format
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

### Update Playlist
Update a playlist with new songs using smart rotation:
```bash
python src/main.py update playlist_name --count 10 --fresh-days 30
```

### View Playlist
View current contents of a playlist:
```bash
python src/main.py view playlist_name
```

### View Statistics
View detailed statistics about the database and optionally a specific playlist:
```bash
python src/main.py stats
# or for a specific playlist
python src/main.py stats --playlist playlist_name
```

### Sync Playlist
Sync the entire song database to a playlist:
```bash
python src/main.py sync playlist_name
```

### Extract Playlist Contents
Extract playlist contents to a CSV file:
```bash
python src/main.py extract playlist_name
# or specify output file
python src/main.py extract playlist_name --output songs.csv
```

### Clean Database
Clean the database by removing songs that no longer exist in Spotify or whose artists have 1 million or more monthly followers:
```bash
python src/main.py clean
# or do a dry run first
python src/main.py clean --dry-run
```
