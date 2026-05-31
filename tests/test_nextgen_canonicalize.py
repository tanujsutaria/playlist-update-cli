"""Unit tests for src/nextgen/canonicalize.py."""

from __future__ import annotations

from nextgen.canonicalize import canonical_track_id, canonicalize_results, normalize_text


class TestNormalizeText:
    def test_collapses_internal_whitespace(self):
        assert normalize_text("  hello    world  ") == "hello world"

    def test_strips_newlines_and_tabs(self):
        assert normalize_text("a\t b\nc") == "a b c"

    def test_empty(self):
        assert normalize_text("   ") == ""


class TestCanonicalTrackId:
    def test_lowercased_and_joined(self):
        assert canonical_track_id("The Beatles", "Hey Jude") == "the beatles|||hey jude"

    def test_normalizes_whitespace(self):
        assert canonical_track_id("  Foo  Bar ", " Baz  Qux ") == "foo bar|||baz qux"


class TestCanonicalizeResults:
    def test_canonical_shape_and_fields_preserved(self):
        raw = [
            {"song": "Hey Jude", "artist": "The Beatles", "year": 1968, "sources": ["a"]},
        ]
        out = canonicalize_results(raw)
        assert len(out) == 1
        item = out[0]
        assert item["track_id"] == "the beatles|||hey jude"
        assert item["song"] == "Hey Jude"
        assert item["artist"] == "The Beatles"
        # Unrelated fields are preserved.
        assert item["year"] == 1968
        assert item["sources"] == ["a"]

    def test_uses_name_when_song_missing(self):
        raw = [{"name": "Some Track", "artist": "Some Artist"}]
        out = canonicalize_results(raw)
        assert out[0]["song"] == "Some Track"
        assert out[0]["track_id"] == "some artist|||some track"

    def test_normalizes_song_and_artist_whitespace(self):
        raw = [{"song": "  Spaced   Out ", "artist": "  Indie   Band "}]
        out = canonicalize_results(raw)
        assert out[0]["song"] == "Spaced Out"
        assert out[0]["artist"] == "Indie Band"
        assert out[0]["track_id"] == "indie band|||spaced out"

    def test_skips_rows_missing_song_or_artist(self):
        raw = [
            {"song": "", "artist": "Artist"},
            {"song": "Title", "artist": ""},
            {"artist": "Only Artist"},
            {"song": "Only Song"},
        ]
        assert canonicalize_results(raw) == []

    def test_dedup_keeps_entry_with_more_sources(self):
        raw = [
            {"song": "Dup", "artist": "Band", "sources": ["s1"]},
            {"song": "DUP", "artist": "band", "sources": ["s1", "s2", "s3"]},
            {"song": "dup", "artist": "Band", "sources": ["s1", "s2"]},
        ]
        out = canonicalize_results(raw)
        assert len(out) == 1
        # The richest sources entry wins.
        assert out[0]["sources"] == ["s1", "s2", "s3"]
        assert out[0]["track_id"] == "band|||dup"

    def test_dedup_first_wins_when_sources_not_richer(self):
        raw = [
            {"song": "X", "artist": "Y", "tag": "first", "sources": ["a", "b"]},
            {"song": "x", "artist": "y", "tag": "second", "sources": ["c"]},
        ]
        out = canonicalize_results(raw)
        assert len(out) == 1
        assert out[0]["tag"] == "first"

    def test_distinct_tracks_not_merged(self):
        raw = [
            {"song": "A", "artist": "Band"},
            {"song": "B", "artist": "Band"},
        ]
        out = canonicalize_results(raw)
        assert len(out) == 2
        assert {i["track_id"] for i in out} == {"band|||a", "band|||b"}
