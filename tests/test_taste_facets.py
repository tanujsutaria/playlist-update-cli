"""Unit tests for taste_facets — the pure fields_json aggregation layer.

Pins the data-quality rules every taste/stats/profile number depends on:
mood/moods + genre/genres source merging, comma-joined value splitting,
fold-then-dedupe counting, decade parsing that never guesses, the claude:
summary-leak filter, and the deterministic headline/display casing helpers.
Offline; no DB, no Rich.
"""

from __future__ import annotations

import json

from taste_facets import (
    clean_summary,
    decade_histogram,
    decade_of,
    display_name,
    facet_track_counts,
    fold_genre,
    fold_instrument,
    headline_case,
    parse_fields,
    split_values,
    taste_title,
)


def _fields(*entries):
    """Build a fields_json blob from (field, value) pairs."""
    return json.dumps([{"field": f, "value": v, "strict": True} for f, v in entries])


class TestSplitValues:
    def test_splits_strips_lowercases_and_drops_empties(self):
        assert split_values(" Dream Pop , shoegaze ,, ") == ["dream pop", "shoegaze"]

    def test_single_value(self):
        assert split_values("Indie Rock") == ["indie rock"]


class TestParseFields:
    def test_merges_genre_and_genres_sources_with_per_track_dedupe(self):
        raw = _fields(("genre", "indie rock, dream pop"), ("genres", "Indie Rock, shoegaze"))
        parsed = parse_fields(raw)
        # A value tagged under both raw fields counts once, first-seen order kept.
        assert parsed["genres"] == ["indie rock", "dream pop", "shoegaze"]

    def test_merges_mood_and_moods_sources(self):
        raw = _fields(("mood", "melancholic"), ("moods", "melancholic, dreamy"))
        assert parse_fields(raw)["moods"] == ["melancholic", "dreamy"]

    def test_preserves_first_seen_order(self):
        raw = _fields(("moods", "cathartic, dreamy, nostalgic"))
        assert parse_fields(raw)["moods"] == ["cathartic", "dreamy", "nostalgic"]

    def test_malformed_json_returns_empty_dict(self):
        assert parse_fields("not json{{") == {}
        assert parse_fields("") == {}
        assert parse_fields('{"field": "moods"}') == {}  # not a list

    def test_summary_never_appears(self):
        raw = _fields(("summary", "a dreamy record"), ("moods", "dreamy"))
        parsed = parse_fields(raw)
        assert "summary" not in parsed
        assert parsed["moods"] == ["dreamy"]

    def test_non_string_values_skipped(self):
        raw = json.dumps([{"field": "moods", "value": 42}, {"field": "moods", "value": "dark"}])
        assert parse_fields(raw)["moods"] == ["dark"]


class TestFoldGenre:
    def test_spelling_variants_share_one_fold_key(self):
        assert fold_genre("synthpop") == fold_genre("synth-pop") == fold_genre("synth pop")
        assert fold_genre("indie-rock") == fold_genre("indie rock")
        assert fold_genre("art-pop") == fold_genre("art pop")

    def test_genre_table_folds_slash_duplicates(self):
        assert fold_genre("alternative/indie rock") == fold_genre("indie rock")

    def test_distinct_genres_stay_distinct(self):
        assert fold_genre("dream pop") != fold_genre("indie pop")


class TestFoldInstrument:
    def test_plurals_and_synonyms_fold(self):
        assert fold_instrument("guitars") == "guitar"
        assert fold_instrument("synths") == "synth"
        assert fold_instrument("synthesizer") == "synth"
        assert fold_instrument("synthesizers") == "synth"
        assert fold_instrument("keys") == "keyboards"
        assert fold_instrument("keyboard") == "keyboards"
        assert fold_instrument("bass guitar") == "bass"

    def test_acoustic_guitar_stays_distinct(self):
        assert fold_instrument("acoustic guitar") == "acoustic guitar"
        assert fold_instrument("acoustic guitar") != fold_instrument("guitars")


class TestFacetTrackCounts:
    def test_counts_tracks_not_entries(self):
        # One track tagged melancholic three ways (both raw fields) counts once.
        rows = [
            ("t1", _fields(("mood", "melancholic, melancholic"), ("moods", "melancholic"))),
            ("t2", _fields(("moods", "melancholic"))),
        ]
        assert facet_track_counts(rows, "moods") == [("melancholic", 2)]

    def test_per_track_dedupe_happens_after_folding(self):
        rows = [("t1", _fields(("genres", "synthpop, synth-pop")))]
        counts = facet_track_counts(rows, "genres", fold=fold_genre)
        assert len(counts) == 1
        assert counts[0][1] == 1  # one track, despite two surface spellings

    def test_display_label_is_most_frequent_surface_form(self):
        rows = [
            ("t1", _fields(("genres", "synth-pop"))),
            ("t2", _fields(("genres", "synth-pop"))),
            ("t3", _fields(("genres", "synthpop"))),
        ]
        assert facet_track_counts(rows, "genres", fold=fold_genre) == [("synth-pop", 3)]

    def test_scope_filter_honored(self):
        rows = [
            ("t1", _fields(("moods", "dreamy"))),
            ("t2", _fields(("moods", "dreamy"))),
            ("t3", _fields(("moods", "dark"))),
        ]
        counts = facet_track_counts(rows, "moods", track_ids={"t1", "t3"})
        assert counts == [("dark", 1), ("dreamy", 1)]

    def test_sort_is_count_desc_then_label_asc(self):
        rows = [
            ("t1", _fields(("moods", "dreamy, dark"))),
            ("t2", _fields(("moods", "dreamy, dark"))),
            ("t3", _fields(("moods", "anthemic"))),
        ]
        # dark/dreamy tie at 2 -> alphabetical; anthemic trails on count.
        assert facet_track_counts(rows, "moods") == [
            ("dark", 2),
            ("dreamy", 2),
            ("anthemic", 1),
        ]


class TestDecadeOf:
    def test_decade_tokens_win(self):
        assert decade_of(["2010s indie"]) == "2010s"
        assert decade_of(["late 2010s"]) == "2010s"

    def test_year_maps_to_decade(self):
        assert decade_of(["2024"]) == "2020s"
        assert decade_of(["released in 1997"]) == "1990s"

    def test_modal_across_values(self):
        assert decade_of(["2010s", "2010s", "1990s"]) == "2010s"

    def test_tie_goes_to_most_recent(self):
        assert decade_of(["1990s", "2010s"]) == "2010s"

    def test_unparseable_is_none_never_guessed(self):
        assert decade_of(["post-punk revival"]) is None
        assert decade_of(["reagan-era"]) is None
        assert decade_of([]) is None

    def test_out_of_bound_and_cross_decade_ranges_do_not_parse(self):
        # Years bounded to 19xx/20xx: an out-of-bound year poisons the value.
        assert decade_of(["set in 1860-1910 (conceptual)"]) is None
        # A cross-decade range is ambiguous — unbucketable beats guessed.
        assert decade_of(["1995-2005"]) is None

    def test_same_decade_year_range_parses(self):
        assert decade_of(["2017-2018"]) == "2010s"


class TestDecadeHistogram:
    def test_buckets_datable_unbucketable(self):
        rows = [
            ("t1", _fields(("era", "2010s"))),
            ("t2", _fields(("era", "2020s"))),
            ("t3", _fields(("era", "2020s indie"))),
            ("t4", _fields(("era", "reagan-era"))),
            ("t5", _fields(("moods", "dreamy"))),  # no era values: not counted at all
        ]
        buckets, datable, unbucketable = decade_histogram(rows)
        assert buckets == [("2020s", 2), ("2010s", 1)]
        assert datable == 3
        assert unbucketable == 1

    def test_count_ties_break_most_recent_first(self):
        rows = [
            ("t1", _fields(("era", "1970s"))),
            ("t2", _fields(("era", "2000s"))),
        ]
        buckets, _, _ = decade_histogram(rows)
        assert buckets == [("2000s", 1), ("1970s", 1)]

    def test_scope_filter(self):
        rows = [("t1", _fields(("era", "2010s"))), ("t2", _fields(("era", "1990s")))]
        buckets, datable, _ = decade_histogram(rows, track_ids={"t2"})
        assert buckets == [("1990s", 1)]
        assert datable == 1


class TestCleanSummary:
    def test_drops_claude_prefixed_entries_case_insensitive(self):
        fields = [
            {"field": "summary", "value": "Claude: judge rationale leak"},
            {"field": "summary", "value": "  claude: another leak"},
            {"field": "summary", "value": "a hazy dream-pop record"},
        ]
        assert clean_summary(fields) == "a hazy dream-pop record"

    def test_none_when_all_leak(self):
        fields = [{"field": "summary", "value": "claude: only leaks here"}]
        assert clean_summary(fields) is None

    def test_none_without_summary_entries(self):
        assert clean_summary([{"field": "moods", "value": "dreamy"}]) is None


class TestTasteTitle:
    def test_needs_top_mood_and_genre_at_five_tracks(self):
        assert taste_title([("introspective", 4)], [("indie rock", 10)]) is None
        assert taste_title([("introspective", 10)], [("indie rock", 4)]) is None
        assert taste_title([], []) is None

    def test_base_title(self):
        title = taste_title([("introspective", 6)], [("indie rock", 10)])
        assert title == "introspective indie rock"

    def test_undertow_fires_at_exactly_35_percent(self):
        title = taste_title(
            [("introspective", 6)], [("indie rock", 20), ("post-punk", 7)]
        )  # 7 == 0.35 * 20
        assert title == "introspective indie rock, with a post-punk undertow"

    def test_no_undertow_below_threshold(self):
        title = taste_title([("introspective", 6)], [("indie rock", 20), ("post-punk", 6)])
        assert title == "introspective indie rock"


class TestHeadlineCase:
    def test_masthead_casing(self):
        assert (
            headline_case("introspective indie rock, with a post-punk undertow")
            == "Introspective Indie Rock, with a Post-Punk Undertow"
        )

    def test_first_word_always_capitalized(self):
        assert headline_case("the cure") == "The Cure"


class TestDisplayName:
    def test_lowercased_names_get_capwords(self):
        assert display_name("wild nothing") == "Wild Nothing"

    def test_apostrophes_survive_capwords_not_title(self):
        assert display_name("let's eat grandma") == "Let's Eat Grandma"

    def test_already_cased_names_pass_through(self):
        assert display_name("Fontaines D.C.") == "Fontaines D.C."
        assert display_name("DIIV") == "DIIV"
