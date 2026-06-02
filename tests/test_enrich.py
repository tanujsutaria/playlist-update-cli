"""Unit + orchestration tests for semantic enrichment (/enrich).

`enrich_track` fetches context via the deep-search providers and re-embeds the
track from that context; `PlaylistCLI.enrich_library` drives it over the library.
Fully offline: `nextgen.enrich.run_providers` is monkeypatched so no subprocess
or network runs, and `EmbeddingModel` uses the conftest sentence-transformers stub.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import ui
from main import PlaylistCLI
from nextgen import enrich as enrich_mod
from nextgen.canonicalize import canonical_track_id
from nextgen.enrich import _match_target, _normalize, enrich_track
from nextgen.providers import ProviderRun
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories


@pytest.fixture(autouse=True)
def _no_sink():
    ui.set_output_sink(None)
    yield
    ui.set_output_sink(None)


def _repos(tmp_path, tracks):
    """tracks: list of (track_id, name, artist_id, artist_name)."""
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    artists = {artist_id: artist_name for _, _, artist_id, artist_name in tracks}
    conn.executemany("INSERT INTO artists (artist_id, name) VALUES (?, ?)", list(artists.items()))
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, ?, 'candidate')",
        [(tid, name, artist_id) for tid, name, artist_id, _ in tracks],
    )
    conn.commit()
    return Repositories(conn)


def _provider_run(results):
    return ProviderRun(results=results, providers=["stub"], summary="", constraints={}, policy={})


def _item(song, artist):
    return {
        "song": song,
        "artist": artist,
        "year": "2012",
        "summary": "Dreamy reverb-soaked indie pop.",
        "sources": ["https://example.com/a"],
        "context": {"genres": ["dream pop"], "moods": ["dreamy", "nostalgic"]},
    }


def _echo_provider(query, **kwargs):
    """Return a provider result matching whatever 'NAME by ARTIST' query came in."""
    song, _, artist = query.partition(" by ")
    return _provider_run([_item(song, artist)])


class TestEnrichTrack:
    def test_enriches_matching_track(self, tmp_path, monkeypatch):
        repos = _repos(
            tmp_path, [("wild nothing|||alpha", "Alpha", "wild nothing", "Wild Nothing")]
        )
        monkeypatch.setattr(enrich_mod, "run_providers", _echo_provider)
        ok = enrich_track(
            repos,
            track_id="wild nothing|||alpha",
            name="Alpha",
            artist="Wild Nothing",
            model_name="all-mpnet-base-v2",
            strict_threshold=0.6,
            lenient_threshold=0.75,
        )
        assert ok is True
        ctx = repos.context.get("wild nothing|||alpha")
        assert ctx is not None and ctx["context_text"]
        # The re-embed input is context-derived, not the lexical "name by artist".
        text = ctx["context_text"].lower()
        assert "dream pop" in text or "dreamy" in text
        emb = repos.embeddings.get("wild nothing|||alpha")
        assert emb is not None
        assert emb["model_name"] == "all-mpnet-base-v2"
        assert emb["embedding_blob"]

    def test_no_match_writes_nothing(self, tmp_path, monkeypatch):
        repos = _repos(
            tmp_path, [("wild nothing|||alpha", "Alpha", "wild nothing", "Wild Nothing")]
        )
        # Provider surfaces a different song -> no confident match -> leave as-is.
        monkeypatch.setattr(
            enrich_mod,
            "run_providers",
            lambda query, **kw: _provider_run([_item("Other Song", "Other Artist")]),
        )
        ok = enrich_track(
            repos,
            track_id="wild nothing|||alpha",
            name="Alpha",
            artist="Wild Nothing",
            model_name="all-mpnet-base-v2",
            strict_threshold=0.6,
            lenient_threshold=0.75,
        )
        assert ok is False
        assert repos.context.get("wild nothing|||alpha") is None
        assert repos.embeddings.get("wild nothing|||alpha") is None

    def test_match_is_whitespace_robust(self, tmp_path, monkeypatch):
        repos = _repos(
            tmp_path, [("wild nothing|||alpha", "Alpha", "wild nothing", "Wild Nothing")]
        )
        # Provider returns an artist with irregular spacing; canonical match still binds.
        monkeypatch.setattr(
            enrich_mod,
            "run_providers",
            lambda query, **kw: _provider_run([_item("Alpha", "Wild   Nothing")]),
        )
        ok = enrich_track(
            repos,
            track_id="wild nothing|||alpha",
            name="Alpha",
            artist="Wild Nothing",
            model_name="all-mpnet-base-v2",
            strict_threshold=0.6,
            lenient_threshold=0.75,
        )
        assert ok is True


class TestEnrichLibrary:
    def _cli(self, repos):
        cli = PlaylistCLI.__new__(PlaylistCLI)
        cli._repos = repos
        # Stub the pipeline so model/thresholds are fixed without touching env.
        cli._search_pipeline = SimpleNamespace(
            model_name="all-mpnet-base-v2",
            strict_threshold=0.6,
            lenient_threshold=0.75,
        )
        return cli

    def test_enriches_up_to_limit(self, tmp_path, monkeypatch):
        repos = _repos(
            tmp_path,
            [
                ("a|||one", "One", "a", "A"),
                ("a|||three", "Three", "a", "A"),
                ("a|||two", "Two", "a", "A"),
            ],
        )
        monkeypatch.setattr(enrich_mod, "run_providers", _echo_provider)
        cli = self._cli(repos)
        enriched = cli.enrich_library(limit=2)
        assert enriched == 2  # bounded by --limit
        count = repos.conn.execute("SELECT COUNT(*) FROM track_context").fetchone()[0]
        assert count == 2

    def test_dry_run_calls_nothing(self, tmp_path, monkeypatch):
        repos = _repos(tmp_path, [("a|||one", "One", "a", "A")])
        calls = {"n": 0}

        def _spy(query, **kw):
            calls["n"] += 1
            return _echo_provider(query)

        monkeypatch.setattr(enrich_mod, "run_providers", _spy)
        cli = self._cli(repos)
        assert cli.enrich_library(limit=10, dry_run=True) == 0
        assert calls["n"] == 0  # no provider/network calls in a dry run
        assert repos.conn.execute("SELECT COUNT(*) FROM track_context").fetchone()[0] == 0

    def test_nothing_to_enrich_when_context_present(self, tmp_path, monkeypatch):
        repos = _repos(tmp_path, [("a|||one", "One", "a", "A")])
        repos.context.upsert(
            {
                "track_id": "a|||one",
                "context_text": "already enriched",
                "strict_text": "",
                "lenient_text": "",
                "fields_json": "[]",
                "sources_json": "[]",
                "strict_ratio": 1.0,
                "context_version": "v1",
                "generated_at": "2026-01-01T00:00:00Z",
            }
        )
        repos.conn.commit()
        monkeypatch.setattr(enrich_mod, "run_providers", _echo_provider)
        cli = self._cli(repos)
        assert cli.enrich_library(limit=10) == 0

    def test_failed_track_is_counted_not_fatal(self, tmp_path, monkeypatch):
        repos = _repos(tmp_path, [("a|||one", "One", "a", "A"), ("a|||two", "Two", "a", "A")])

        def _flaky(query, **kw):
            if query.startswith("One"):
                raise RuntimeError("provider down")
            return _echo_provider(query)

        monkeypatch.setattr(enrich_mod, "run_providers", _flaky)
        cli = self._cli(repos)
        # One raises (counted as failed), Two still enriches — the loop survives.
        assert cli.enrich_library(limit=10) == 1


def _it(song, artist):
    """A canonicalized provider result item (track_id mirrors what canonicalize sets)."""
    return {"song": song, "artist": artist, "track_id": canonical_track_id(artist, song)}


class TestMatchTarget:
    """High-confidence matcher: absorbs cross-source spelling variance, but never
    binds a low-confidence guess (precision over recall)."""

    def test_exact(self):
        items = [_it("Vices", "5ilas & Shimmer Johnson")]
        assert _match_target(items, "5ilas & Shimmer Johnson", "Vices") is items[0]

    def test_apostrophe(self):
        items = [_it("Aint", "Some Artist")]
        assert _match_target(items, "Some Artist", "Ain't") is items[0]

    def test_curly_apostrophe(self):  # the real DB data uses U+2019
        items = [_it("Aint", "Some Artist")]
        assert _match_target(items, "Some Artist", "Ain’t") is items[0]

    def test_ampersand_vs_and(self):
        items = [_it("Salt and Pepper", "Duo")]
        assert _match_target(items, "Duo", "Salt & Pepper") is items[0]

    def test_feat_in_artist(self):
        # Real case: DB artist "Amaarae"; source returns "Amaarae (feat. Moliy)".
        items = [_it("Sad Girlz Luv Money", "Amaarae (feat. Moliy)")]
        assert _match_target(items, "Amaarae", "Sad Girlz Luv Money") is items[0]

    def test_remaster_suffix(self):
        items = [_it("Heroes - 2017 Remaster", "David Bowie")]
        assert _match_target(items, "David Bowie", "Heroes") is items[0]

    def test_diacritics(self):
        items = [_it("Crazy In Love", "Beyoncé")]
        assert _match_target(items, "Beyonce", "Crazy In Love") is items[0]

    def test_fuzzy_typo_within_threshold(self):
        items = [_it("The Underside of Powers", "Algiers")]  # trailing-s typo
        assert _match_target(items, "Algiers", "The Underside of Power") is items[0]

    def test_distinct_song_not_matched(self):
        items = [_it("Devices", "5ilas & Shimmer Johnson")]
        assert _match_target(items, "5ilas & Shimmer Johnson", "Vices") is None

    def test_wrong_artist_not_matched(self):
        items = [_it("Vices", "Totally Different Band")]
        assert _match_target(items, "5ilas & Shimmer Johnson", "Vices") is None

    def test_no_items(self):
        assert _match_target([], "X", "Y") is None

    def test_picks_correct_among_several(self):
        items = [
            _it("Some Other Song", "Another Artist"),
            _it("Vices", "5ilas & Shimmer Johnson"),
            _it("Devices", "5ilas & Shimmer Johnson"),
        ]
        assert _match_target(items, "5ilas & Shimmer Johnson", "Vices")["song"] == "Vices"


class TestNormalize:
    def test_examples(self):
        assert _normalize("Beyoncé") == "beyonce"
        assert _normalize("Salt & Pepper") == "salt and pepper"
        assert _normalize("Ain't") == "aint"
        assert _normalize("Ain’t") == "aint"
        assert _normalize("Heroes - 2017 Remaster") == "heroes"
        assert _normalize("Song (feat. X)") == "song"
