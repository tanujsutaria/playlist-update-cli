from __future__ import annotations


def initial_schema() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS schema_version (
          version INTEGER NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS artists (
          artist_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          genres_json TEXT,
          popularity INTEGER,
          updated_at TEXT
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS tracks (
          track_id TEXT PRIMARY KEY,
          spotify_id TEXT,
          name TEXT NOT NULL,
          artist_id TEXT,
          album_name TEXT,
          release_date TEXT,
          duration_ms INTEGER,
          explicit INTEGER,
          popularity INTEGER,
          spotify_url TEXT,
          status TEXT DEFAULT 'candidate',
          last_decision TEXT,
          decision_reason TEXT,
          created_at TEXT,
          updated_at TEXT,
          FOREIGN KEY (artist_id) REFERENCES artists(artist_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS track_context (
          track_id TEXT PRIMARY KEY,
          context_text TEXT,
          strict_text TEXT,
          lenient_text TEXT,
          fields_json TEXT,
          sources_json TEXT,
          strict_ratio REAL,
          context_version TEXT,
          generated_at TEXT,
          FOREIGN KEY (track_id) REFERENCES tracks(track_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS track_embeddings (
          track_id TEXT PRIMARY KEY,
          model_name TEXT NOT NULL,
          embedding_blob BLOB NOT NULL,
          embedding_dim INTEGER NOT NULL,
          embedding_norm REAL,
          strict_ratio REAL,
          created_at TEXT,
          FOREIGN KEY (track_id) REFERENCES tracks(track_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS queries (
          query_hash TEXT PRIMARY KEY,
          query_text TEXT NOT NULL,
          constraints_json TEXT,
          embedding_blob BLOB,
          embedding_dim INTEGER,
          model_name TEXT,
          created_at TEXT,
          last_used_at TEXT
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS search_runs (
          run_id TEXT PRIMARY KEY,
          query_hash TEXT NOT NULL,
          provider TEXT NOT NULL,
          expanded INTEGER DEFAULT 0,
          status TEXT,
          error TEXT,
          started_at TEXT,
          finished_at TEXT,
          results_count INTEGER,
          FOREIGN KEY (query_hash) REFERENCES queries(query_hash)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS search_candidates (
          run_id TEXT NOT NULL,
          track_id TEXT NOT NULL,
          rank INTEGER,
          score_text REAL,
          score_audio REAL,
          score_final REAL,
          strict_ratio REAL,
          lenient_ratio REAL,
          sources_count INTEGER,
          PRIMARY KEY (run_id, track_id),
          FOREIGN KEY (run_id) REFERENCES search_runs(run_id),
          FOREIGN KEY (track_id) REFERENCES tracks(track_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS track_sources (
          source_id TEXT PRIMARY KEY,
          track_id TEXT NOT NULL,
          url TEXT,
          title TEXT,
          snippet TEXT,
          provider TEXT,
          is_strict INTEGER DEFAULT 1,
          retrieved_at TEXT,
          FOREIGN KEY (track_id) REFERENCES tracks(track_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_tracks_spotify_id ON tracks(spotify_id);",
        "CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks(artist_id);",
        "CREATE INDEX IF NOT EXISTS idx_context_strict_ratio ON track_context(strict_ratio);",
        "CREATE INDEX IF NOT EXISTS idx_search_runs_query ON search_runs(query_hash);",
        "CREATE INDEX IF NOT EXISTS idx_search_candidates_track ON search_candidates(track_id);",
    ]


def schema_v2() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS listen_events (
          event_id TEXT PRIMARY KEY,
          track_id TEXT NOT NULL,
          spotify_id TEXT,
          played_at TEXT,
          source TEXT,
          created_at TEXT,
          FOREIGN KEY (track_id) REFERENCES tracks(track_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_listen_events_track ON listen_events(track_id);",
        "CREATE INDEX IF NOT EXISTS idx_listen_events_played_at ON listen_events(played_at);",
    ]


def schema_v3() -> list[str]:
    return [
        "ALTER TABLE search_runs ADD COLUMN score_config_hash TEXT;",
    ]


def schema_v4() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS playlists (
          playlist_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          spotify_playlist_id TEXT,
          current_generation INTEGER NOT NULL DEFAULT 0,
          created_at TEXT,
          updated_at TEXT
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS rotation_generations (
          generation_id TEXT PRIMARY KEY,
          playlist_id TEXT NOT NULL,
          generation_index INTEGER NOT NULL,
          created_at TEXT,
          UNIQUE (playlist_id, generation_index),
          FOREIGN KEY (playlist_id) REFERENCES playlists(playlist_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS generation_tracks (
          generation_id TEXT NOT NULL,
          track_id TEXT NOT NULL,
          position INTEGER NOT NULL,
          PRIMARY KEY (generation_id, track_id),
          FOREIGN KEY (generation_id) REFERENCES rotation_generations(generation_id),
          FOREIGN KEY (track_id) REFERENCES tracks(track_id)
        );
        """,
        (
            "CREATE INDEX IF NOT EXISTS idx_rotation_generations_playlist "
            "ON rotation_generations(playlist_id, generation_index);"
        ),
        "CREATE INDEX IF NOT EXISTS idx_generation_tracks_track ON generation_tracks(track_id);",
    ]


def schema_v5() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS track_sonic (
          track_id TEXT PRIMARY KEY,
          mbid TEXT,
          sonic_blob BLOB NOT NULL,
          sonic_dim INTEGER NOT NULL,
          features_json TEXT,
          source TEXT,
          created_at TEXT,
          FOREIGN KEY (track_id) REFERENCES tracks(track_id)
        );
        """,
    ]


def schema_v6() -> list[str]:
    # Additive, nullable columns (same shape as the schema_v3 ALTER precedent) so
    # deep-search can persist the synthesized summary per run and the per-track
    # metrics per candidate — letting cache hits re-surface the summary and the
    # constraint filter act on cached runs.
    return [
        "ALTER TABLE search_runs ADD COLUMN summary TEXT;",
        "ALTER TABLE search_candidates ADD COLUMN metrics_json TEXT;",
    ]


def schema_v7() -> list[str]:
    # Spotify library sync: a local mirror of remote playlists / liked songs,
    # one cursor row per sync source, and richer listen_events telemetry so a
    # GDPR export and live polling can enrich the same event rows.
    return [
        """
        CREATE TABLE IF NOT EXISTS sync_state (
          source TEXT PRIMARY KEY,
          cursor TEXT,
          last_synced_at TEXT
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS spotify_playlists (
          spotify_playlist_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          owner TEXT,
          is_owned INTEGER DEFAULT 0,
          snapshot_id TEXT,
          total_tracks INTEGER,
          synced_at TEXT
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS playlist_tracks (
          spotify_playlist_id TEXT NOT NULL
            REFERENCES spotify_playlists(spotify_playlist_id) ON DELETE CASCADE,
          track_id TEXT NOT NULL REFERENCES tracks(track_id),
          added_at TEXT,
          position INTEGER,
          synced_at TEXT,
          PRIMARY KEY (spotify_playlist_id, track_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS liked_tracks (
          track_id TEXT PRIMARY KEY REFERENCES tracks(track_id),
          added_at TEXT,
          synced_at TEXT
        );
        """,
        "ALTER TABLE listen_events ADD COLUMN ms_played INTEGER;",
        "ALTER TABLE listen_events ADD COLUMN skipped INTEGER;",
        "ALTER TABLE listen_events ADD COLUMN context_uri TEXT;",
        "CREATE INDEX IF NOT EXISTS idx_playlist_tracks_track ON playlist_tracks(track_id);",
        "CREATE INDEX IF NOT EXISTS idx_liked_tracks_added ON liked_tracks(added_at);",
    ]
