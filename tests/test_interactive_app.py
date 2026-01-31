"""
Unit tests for interactive app command gating.
"""
import os

from arg_parse import setup_parsers
from interactive_app import PlaylistInteractiveApp, SPOTIFY_REQUIRED_KEYS
from main import PlaylistCLI


class DummyApp(PlaylistInteractiveApp):
    def __init__(self, cli, parser):
        super().__init__(cli=cli, parser=parser)
        self.logged = []
        self.commands = []

    def append_log(self, renderable) -> None:
        self.logged.append(renderable)

    def _run_command(self, command: str, args: object) -> None:
        self.commands.append(command)


def _clear_spotify_env(monkeypatch):
    for key in SPOTIFY_REQUIRED_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_setup_mode_blocks_spotify_commands(monkeypatch):
    _clear_spotify_env(monkeypatch)
    app = DummyApp(cli=PlaylistCLI(), parser=setup_parsers())
    app._refresh_env_status()

    app._handle_command('/update "My Playlist"')

    assert app.commands == []
    assert app.logged, "Expected a setup warning to be logged."


def test_setup_mode_allows_backup(monkeypatch):
    _clear_spotify_env(monkeypatch)
    app = DummyApp(cli=PlaylistCLI(), parser=setup_parsers())
    app._refresh_env_status()

    app._handle_command("/backup")

    assert app.commands == ["backup"]
