# Spotify Playlist Manager

A Python script to manage Spotify playlists through the command line, featuring intelligent song rotation and similarity-based recommendations.

## Features
- Import songs from CSV files into local database
- Automatically rotate songs in playlists using smart selection
- Find similar songs using AI-powered embeddings
- View detailed playlist and rotation statistics
- Sync entire song database to a playlist
- Track playlist history and rotation progress

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
   pip install -r requirements.txt
   ```

4. Run initial setup:
   ```bash
   python src/setup.py
   ```

## Usage

### Import Songs
Import songs from a CSV file (format: name,artist):

```bash
python src/main.py import songs.csv
```

### Update Playlist
Update a playlist with new songs using smart rotation:
```bash
python src/main.py update playlist_name
```

### View Playlist Statistics
View detailed statistics about a playlist:
```bash
python src/main.py stats playlist_name
```

### Sync Playlist
Sync the entire song database to a playlist:
```bash
python src/main.py sync playlist_name
```

### Track Playlist Progress
Track the progress of song rotation in a playlist:
```bash
python src/main.py track playlist_name
```

### Extract Playlist Contents
Extract playlist contents to a file:
```bash
python src/main.py extract playlist_name
``` 