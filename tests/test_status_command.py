"""Tests for the /status command (one-screen read-only snapshot).

Offline by construction: temp SQLite DBs, no Spotify credentials, no network.
The auth section reads via ``cached_token_summary`` — the offline token-file
reader — pointed at a temp path (or monkeypatched), never the real cache and
never spotipy's validate/refresh path.
"""

from __future__ import annotations

import argparse
import json
import os
import re

import pytest

import main
import spotify_manager
import ui
from arg_parse import parse_tokens
from interactive_app import COMMANDS_ALLOWED_WITHOUT_SPOTIFY, HELP_GROUPS
from main import PlaylistCLI, dispatch_command
from spotify_manager import SPOTIFY_ENV_KEYS, SPOTIFY_SCOPES
from storage.db import Database
from storage.migrations import LATEST_VERSION, ensure_schema
from storage.repos import Repositories

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(out: str) -> str:
    """Strip ANSI codes and panel borders, then collapse whitespace, so phrases
    wrapped across console lines (or folded inside a panel) can still be
    matched as substrings."""
    out = _ANSI.sub("", out)
    out = re.sub(r"[│╭╮╰╯]", " ", out)
    return re.sub(r"\s+", " ", out)


@pytest.fixture(autouse=True)
def _no_sink():
    """show_status renders via ui._emit; with no sink it prints to the console
    so capsys can read it. Ensure no stray sink leaks in from another test."""
    ui.set_output_sink(None)
    yield
    ui.set_output_sink(None)


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch, tmp_path):
    """The no-credentials baseline every test starts from: no Spotify env keys,
    no provider env/API keys, and the token cache pointed at an empty temp dir
    (so the real offline reader runs without touching the repo's cache)."""
    for key in list(os.environ):
        if key.startswith(("WEB_SEARCH_", "WEB_SCORE_")):
            monkeypatch.delenv(key)
    for key in (*SPOTIFY_ENV_KEYS, "ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        spotify_manager, "_token_cache_path", lambda: tmp_path / "cache" / ".spotify_token"
    )


def _fresh_cli(tmp_path):
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._storage = db
    cli._repos = Repositories(conn)
    return cli, conn


def _seed_library(conn) -> None:
    """3 tracks (1 with a spotify id), 1 enriched, 1 embedded, 2 listens,
    1 mirrored playlist, 1 rotation playlist with 2 generations."""
    conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a1', 'Wild Nothing')")
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, spotify_id, status) "
        "VALUES (?, ?, 'a1', ?, 'candidate')",
        [
            ("wild nothing|||a", "a", "sp1"),
            ("wild nothing|||b", "b", None),
            ("wild nothing|||c", "c", None),
        ],
    )
    conn.execute("INSERT INTO track_context (track_id) VALUES ('wild nothing|||a')")
    conn.execute(
        "INSERT INTO track_embeddings (track_id, model_name, embedding_blob, embedding_dim) "
        "VALUES ('wild nothing|||a', 'stub', X'00', 1)"
    )
    conn.executemany(
        "INSERT INTO listen_events (event_id, track_id) VALUES (?, 'wild nothing|||a')",
        [("e1",), ("e2",)],
    )
    conn.execute(
        "INSERT INTO spotify_playlists (spotify_playlist_id, name) VALUES ('spl', 'Mirrored')"
    )
    conn.execute(
        "INSERT INTO playlists (playlist_id, name, current_generation, updated_at) "
        "VALUES ('pl', 'mix', 2, '2026-01-01T00:00:00')"
    )
    conn.executemany(
        "INSERT INTO rotation_generations (generation_id, playlist_id, generation_index) "
        "VALUES (?, 'pl', ?)",
        [("g0", 0), ("g1", 1)],
    )


class TestEmptyFreshDb:
    def test_counts_and_empty_notices(self, tmp_path, capsys):
        cli, _ = _fresh_cli(tmp_path)

        payload = cli.show_status()

        assert payload["storage"]["tracks"] == 0
        assert payload["storage"]["schema_version"] == LATEST_VERSION
        assert payload["storage"]["listen_events"] == 0
        assert payload["storage"]["db_path"] == str(tmp_path / "tunr.db")
        out = _flat(capsys.readouterr().out)
        assert "Library is empty" in out
        assert "Listen history is empty" in out
        assert f"v{LATEST_VERSION}" in out

    def test_config_section_without_playlists_or_providers(self, tmp_path, capsys):
        cli, _ = _fresh_cli(tmp_path)

        payload = cli.show_status()

        assert payload["config"]["active_playlist"] is None
        assert payload["config"]["search_providers"] == []
        assert payload["config"]["provider_env_keys"] == []
        out = _flat(capsys.readouterr().out)
        assert "none yet" in out


class TestPopulatedCounts:
    def test_storage_counts(self, tmp_path, capsys):
        cli, conn = _fresh_cli(tmp_path)
        _seed_library(conn)

        payload = cli.show_status()

        assert payload["storage"] == {
            "db_path": str(tmp_path / "tunr.db"),
            "schema_version": LATEST_VERSION,
            "tracks": 3,
            "contexts": 1,
            "embeddings": 1,
            "sonic": 0,
            "listen_events": 2,
            "mirrored_playlists": 1,
            "rotation_playlists": 1,
            "rotation_generations": 2,
        }
        out = _flat(capsys.readouterr().out)
        assert "1/3" in out  # enriched contexts / tracks
        assert "1 (2 generations)" in out

    def test_gaps_and_remedies_reuse_profile_logic(self, tmp_path, capsys):
        cli, conn = _fresh_cli(tmp_path)
        _seed_library(conn)

        payload = cli.show_status()

        assert payload["coverage"]["backfill"] == {
            "missing_embeddings": 2,
            "missing_context": 2,
            "missing_sonic": 3,
            "missing_spotify_id": 2,
        }
        out = _flat(capsys.readouterr().out)
        assert "2 unenriched" in out
        assert "/enrich backfills context + embeddings" in out
        assert "Listen history is empty" not in out  # 2 events seeded

    def test_active_playlist_row(self, tmp_path, capsys):
        cli, conn = _fresh_cli(tmp_path)
        _seed_library(conn)

        payload = cli.show_status()

        assert payload["config"]["active_playlist"] == "mix"
        out = _flat(capsys.readouterr().out)
        assert "mix (generation 2)" in out


class TestNoCredentialsPath:
    """The auth section must degrade gracefully: no exception, no network,
    no token contents — with nothing configured at all."""

    def test_renders_without_credentials_or_token(self, tmp_path, capsys):
        cli, _ = _fresh_cli(tmp_path)

        rc = dispatch_command(cli, "status", argparse.Namespace(command="status"))

        assert rc == 0
        out = _flat(capsys.readouterr().out)
        assert "missing env: " + ", ".join(SPOTIFY_ENV_KEYS) in out
        assert "no cached token" in out

    def test_provider_env_names_shown_values_never(self, tmp_path, capsys, monkeypatch):
        secret = "super-secret-command-xyz --with-args"
        monkeypatch.setenv("WEB_SEARCH_CMD", secret)
        cli, _ = _fresh_cli(tmp_path)

        payload = cli.show_status()

        assert payload["config"]["provider_env_keys"] == ["WEB_SEARCH_CMD"]
        assert payload["config"]["search_providers"] == ["web"]
        out = _flat(capsys.readouterr().out)
        assert "WEB_SEARCH_CMD" in out
        assert "super-secret" not in out

    def test_token_metadata_rendered_secrets_never(self, tmp_path, capsys):
        token_path = tmp_path / "cache" / ".spotify_token"
        token_path.parent.mkdir(parents=True)
        token_path.write_text(
            json.dumps(
                {
                    "access_token": "fake-access-token-value",
                    "refresh_token": "fake-refresh-token-value",
                    "scope": " ".join(SPOTIFY_SCOPES),
                    "expires_at": 4_000_000_000,  # far future: not expired
                }
            )
        )
        cli, _ = _fresh_cli(tmp_path)

        payload = cli.show_status()

        assert payload["auth"]["token_present"] is True
        assert payload["auth"]["missing_scopes"] == []
        out = _flat(capsys.readouterr().out)
        assert "cached · valid" in out
        assert "all required scopes" in out
        assert "fake-access-token-value" not in out
        assert "fake-refresh-token-value" not in out

    def test_expired_token_and_scope_gap(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(
            main,
            "cached_token_summary",
            lambda: {
                "expires_at": 1_000_000_000,  # long past: expired
                "scope": "playlist-read-private",
                "has_refresh_token": True,
            },
        )
        cli, _ = _fresh_cli(tmp_path)

        payload = cli.show_status()

        assert "user-top-read" in payload["auth"]["missing_scopes"]
        out = _flat(capsys.readouterr().out)
        assert "expired (refreshes on next use)" in out
        assert "missing scopes" in out


class TestCachedTokenSummaryOffline:
    """The offline reader returns metadata only — token strings never leave it."""

    def test_metadata_only(self, tmp_path):
        token_path = tmp_path / "cache" / ".spotify_token"
        token_path.parent.mkdir(parents=True)
        token_path.write_text(
            json.dumps(
                {
                    "access_token": "opaque-a",
                    "refresh_token": "opaque-r",
                    "scope": "user-top-read",
                    "expires_at": 123,
                }
            )
        )

        summary = spotify_manager.cached_token_summary()

        assert summary == {
            "expires_at": 123,
            "scope": "user-top-read",
            "has_refresh_token": True,
        }

    def test_missing_file_is_none(self, tmp_path):
        assert spotify_manager.cached_token_summary() is None


class TestRegistration:
    def test_parser_accepts_bare_status(self):
        command, args, error = parse_tokens(["status"])
        assert error is None
        assert command == "status"

    def test_registered_everywhere(self):
        assert "status" in main._COMMAND_HANDLERS
        assert "status" in COMMANDS_ALLOWED_WITHOUT_SPOTIFY
        assert any("status" in names for _, names in HELP_GROUPS)
