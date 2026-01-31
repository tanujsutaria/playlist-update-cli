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
