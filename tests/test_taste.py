"""Integration tests for PlaylistCLI.show_taste (the /taste profile card).

Seeds a temp SQLite store with known embeddings so the centroid ranking is
deterministic, and asserts the most/least representative tracks land correctly
and the (honest) text-based caveat is shown. Offline; no embedding model.
"""

from __future__ import annotations

import pytest

import ui
from main import PlaylistCLI
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm

# Three tracks cluster on axis 0; one outlier sits on axis 3.
EMB = {
    "wild nothing|||a": [1.0, 0.0, 0.0, 0.0],
    "wild nothing|||b": [0.9, 0.1, 0.0, 0.0],
    "wild nothing|||c": [0.8, 0.2, 0.0, 0.0],
    "outlier artist|||z": [0.0, 0.0, 0.0, 1.0],
}
NAMES = {
    "wild nothing|||a": ("Alpha", "wild nothing", "Wild Nothing"),
    "wild nothing|||b": ("Beta", "wild nothing", "Wild Nothing"),
    "wild nothing|||c": ("Gamma", "wild nothing", "Wild Nothing"),
    "outlier artist|||z": ("Zeta", "outlier artist", "Outlier Artist"),
}


@pytest.fixture(autouse=True)
def _no_sink():
    ui.set_output_sink(None)
    yield
    ui.set_output_sink(None)


def _cli_over(conn) -> PlaylistCLI:
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    return cli


def _sonic_vec():
    from storage.sonic import SONIC_DIM

    v = [0.3] * SONIC_DIM
    v[1] = 0.8  # mood_acoustic
    v[6] = 0.8  # mood_relaxed
    v[11] = 0.35  # bpm_norm -> ~103 BPM
    return v


def _seed(tmp_path, with_rotation=True, with_listens=False, with_sonic=False):
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    artists = {(aid, disp) for _, (_, aid, disp) in NAMES.items()}
    conn.executemany("INSERT INTO artists (artist_id, name) VALUES (?, ?)", list(artists))
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, ?, 'candidate')",
        [(tid, NAMES[tid][0], NAMES[tid][1]) for tid in EMB],
    )
    conn.executemany(
        "INSERT INTO track_embeddings "
        "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (tid, "all-mpnet-base-v2", encode_vector(vec), len(vec), vector_norm(vec))
            for tid, vec in EMB.items()
        ],
    )
    if with_rotation:
        conn.execute(
            "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('pl','mix',0)"
        )
        conn.execute(
            "INSERT INTO rotation_generations (generation_id, playlist_id, generation_index) "
            "VALUES ('g0','pl',0)"
        )
        conn.executemany(
            "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES ('g0', ?, ?)",
            [(tid, i) for i, tid in enumerate(EMB)],
        )
    if with_listens:
        conn.executemany(
            "INSERT INTO listen_events (event_id, track_id, played_at, source) "
            "VALUES (?, ?, ?, 'recently_played')",
            [(f"e{i}", tid, f"2026-05-3{i}T00:00:00Z") for i, tid in enumerate(EMB)],
        )
    if with_sonic:
        svec = _sonic_vec()
        conn.executemany(
            "INSERT INTO track_sonic (track_id, mbid, sonic_blob, sonic_dim, source) "
            "VALUES (?, ?, ?, ?, 'acousticbrainz')",
            [(tid, f"mb-{i}", encode_vector(svec), len(svec)) for i, tid in enumerate(EMB)],
        )
    conn.commit()
    return _cli_over(conn)


class TestShowTaste:
    def test_representative_ranking_is_correct(self, tmp_path, capsys):
        cli = _seed(tmp_path)
        cli.show_taste(top=2)
        out = capsys.readouterr().out
        assert "Your Taste" in out
        assert "Most representative" in out
        # Axis-0 cluster dominates the centroid -> Alpha/Beta are most central.
        assert "Alpha" in out
        # The outlier shows up under the widest-ranging section, not as representative.
        assert "Widest-ranging" in out
        assert "Zeta" in out

    def test_source_and_signal_labels(self, tmp_path, capsys):
        cli = _seed(tmp_path)  # rotation seeded, no listens, no context
        cli.show_taste(top=4)
        out = capsys.readouterr().out
        assert "your rotation" in out  # seeded from generation_tracks
        assert "text-based" in out  # track_context empty -> lexical signal
        assert "/enrich" in out  # honest deepening hint
        assert "Distinct artists" in out

    def test_prefers_recent_plays_when_present(self, tmp_path, capsys):
        from main import TASTE_SEED_FLOOR

        cli = _seed(tmp_path, with_listens=True)
        # The plays seed only wins at TASTE_SEED_FLOOR embedded tracks — pad
        # the played-and-embedded population past it.
        conn = cli.repos.conn
        for i in range(TASTE_SEED_FLOOR):
            tid = f"filler|||track {i}"
            conn.execute("INSERT OR IGNORE INTO artists (artist_id, name) VALUES ('filler','F')")
            conn.execute(
                "INSERT INTO tracks (track_id, name, artist_id, status) "
                "VALUES (?, ?, 'filler', 'candidate')",
                (tid, f"Track {i}"),
            )
            vec = [1.0, 0.1 * i, 0.0, 0.0]
            conn.execute(
                "INSERT INTO track_embeddings "
                "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
                "VALUES (?, 'all-mpnet-base-v2', ?, 4, ?)",
                (tid, encode_vector(vec), vector_norm(vec)),
            )
            conn.execute(
                "INSERT INTO listen_events (event_id, track_id, played_at, source) "
                "VALUES (?, ?, ?, 'recently_played')",
                (f"fill{i}", tid, f"2026-05-2{i % 10}T00:00:00Z"),
            )
        conn.commit()
        cli.show_taste(top=4)
        out = capsys.readouterr().out
        assert "recent plays" in out  # listen_events takes priority over rotation

    def test_starved_plays_seed_falls_back_and_discloses(self, tmp_path, capsys):
        """The live-data regression: a handful of embedded plays (below
        TASTE_SEED_FLOOR) must NOT bail /taste out with 'not enough embedded
        tracks' — it falls through to rotation and says why."""
        from main import TASTE_SEED_FLOOR

        cli = _seed(tmp_path, with_listens=True)  # 4 embedded plays < floor
        assert len(EMB) < TASTE_SEED_FLOOR
        payload = cli.show_taste(top=4)
        out = capsys.readouterr().out
        assert payload is not None
        assert payload["source"] == "your rotation"
        assert "Recent plays carry too few embedded tracks" in out
        assert "Not enough embedded tracks" not in out

    def test_too_few_tracks_message(self, tmp_path, capsys):
        db = Database(tmp_path / "sparse.db")
        conn = db.connect()
        ensure_schema(conn)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a','A')")
        conn.execute(
            "INSERT INTO tracks (track_id, name, artist_id, status) VALUES ('a|||x','X','a','candidate')"
        )
        conn.execute(
            "INSERT INTO track_embeddings "
            "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
            "VALUES ('a|||x','all-mpnet-base-v2',?,2,?)",
            (encode_vector([1.0, 0.0]), vector_norm([1.0, 0.0])),
        )
        conn.commit()
        cli = _cli_over(conn)
        cli.show_taste()
        out = capsys.readouterr().out
        assert "Not enough embedded tracks" in out

    def test_sonic_profile_present_with_sonic_data(self, tmp_path, capsys):
        cli = _seed(tmp_path, with_sonic=True)
        payload = cli.show_taste(top=2)
        out = capsys.readouterr().out
        assert "Your sound" in out
        assert payload["sonic_coverage"] == len(EMB)
        assert payload["sonic_profile"] is not None
        assert payload["sonic_profile"]["mood_relaxed"] == pytest.approx(0.8)
        assert "bpm" in payload["sonic_profile"]

    def test_no_sonic_profile_without_data(self, tmp_path, capsys):
        cli = _seed(tmp_path)  # no track_sonic rows
        payload = cli.show_taste(top=2)
        out = capsys.readouterr().out
        assert "Your sound" not in out
        assert payload["sonic_coverage"] == 0
        assert payload["sonic_profile"] is None
        # Representative ranking is unchanged (degrades to text-only).
        assert "Alpha" in out

    def test_sonic_blend_actually_re_ranks(self, tmp_path, capsys):
        """A track that is text-peripheral but sonic-central is lifted by the blend
        above a track it would beat it on text alone."""
        from storage.sonic import SONIC_DIM

        conn = Database(tmp_path / "rr.db").connect()
        ensure_schema(conn)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'A')")
        text = {
            "a|||central": [1.0, 0.0],
            "a|||near": [0.95, 0.05],
            "a|||near2": [0.9, 0.1],
            "a|||far": [0.0, 1.0],
        }
        names = {
            "a|||central": "Central",
            "a|||near": "Near",
            "a|||near2": "Near2",
            "a|||far": "Far",
        }
        conn.executemany(
            "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, 'a', 'candidate')",
            [(tid, names[tid]) for tid in text],
        )
        conn.executemany(
            "INSERT INTO track_embeddings "
            "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
            "VALUES (?, 'all-mpnet-base-v2', ?, ?, ?)",
            [(tid, encode_vector(v), len(v), vector_norm(v)) for tid, v in text.items()],
        )

        def _s(a, b):
            return [a, b] + [0.0] * (SONIC_DIM - 2)

        sonic = {  # Far sits at the sonic centroid; Near is sonic-peripheral.
            "a|||central": _s(1.0, 0.0),
            "a|||near": _s(0.0, 1.0),
            "a|||near2": _s(0.5, 0.5),
            "a|||far": _s(0.5, 0.5),
        }
        conn.executemany(
            "INSERT INTO track_sonic (track_id, sonic_blob, sonic_dim, source) "
            "VALUES (?, ?, ?, 'acousticbrainz')",
            [(tid, encode_vector(v), len(v)) for tid, v in sonic.items()],
        )
        conn.commit()
        payload = _cli_over(conn).show_taste(top=4)
        order = [r["name"] for r in payload["most_representative"]]
        # On text alone Far is dead last; the sonic blend lifts it above Near.
        assert order.index("Far") < order.index("Near")


# =============================================================================
# Liner Notes sections (masthead, facets, ● column, crowns, core vs frontier,
# insights, payload additivity) — richer seeded fixtures.
# =============================================================================

import json as _json
import re as _re

from storage.sonic import SONIC_DIM

_ANSI = _re.compile(r"\x1b\[[0-9;]*m")


def _flat(out: str) -> str:
    """Strip ANSI styling and panel borders, then collapse whitespace, so
    substring asserts survive console-width wrapping (wrapped styled lines
    re-open their escape codes — and panel-bordered lines interleave `│` —
    mid-phrase)."""
    return " ".join(_ANSI.sub("", out).replace("│", " ").split())


def _fj(genres=None, moods=None, era=None, comparisons=None):
    """Build a fields_json blob in the live entry shape."""
    entries = []
    for field, value in (
        ("genres", genres),
        ("moods", moods),
        ("era", era),
        ("comparisons", comparisons),
    ):
        if value:
            entries.append({"field": field, "value": value, "strict": True})
    return _json.dumps(entries)


def _build(tmp_path, embeddings, context=None, sonic=None, db_name="rich.db"):
    """Seed a CLI with rotation membership for every embedded track.

    embeddings: {track_id: vec}; context: {track_id: fields_json};
    sonic: {track_id: sonic_vec}. Track/artist names derive from the id
    ("artist|||name" -> name "Name", artist display = capitalized prefix).
    """
    db = Database(tmp_path / db_name)
    conn = db.connect()
    ensure_schema(conn)
    artist_ids = {tid.split("|||")[0] for tid in embeddings}
    conn.executemany(
        "INSERT INTO artists (artist_id, name) VALUES (?, ?)",
        [(aid, aid.title()) for aid in artist_ids],
    )
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, ?, 'candidate')",
        [(tid, tid.split("|||")[1].title(), tid.split("|||")[0]) for tid in embeddings],
    )
    conn.executemany(
        "INSERT INTO track_embeddings "
        "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
        "VALUES (?, 'all-mpnet-base-v2', ?, ?, ?)",
        [(tid, encode_vector(v), len(v), vector_norm(v)) for tid, v in embeddings.items()],
    )
    conn.execute(
        "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('pl','mix',0)"
    )
    conn.execute(
        "INSERT INTO rotation_generations (generation_id, playlist_id, generation_index) "
        "VALUES ('g0','pl',0)"
    )
    conn.executemany(
        "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES ('g0', ?, ?)",
        [(tid, i) for i, tid in enumerate(embeddings)],
    )
    for tid, fields_json in (context or {}).items():
        conn.execute(
            "INSERT INTO track_context (track_id, context_text, fields_json) VALUES (?, '', ?)",
            (tid, fields_json),
        )
    for i, (tid, vec) in enumerate((sonic or {}).items()):
        conn.execute(
            "INSERT INTO track_sonic (track_id, mbid, sonic_blob, sonic_dim, source) "
            "VALUES (?, ?, ?, ?, 'acousticbrainz')",
            (tid, f"mb-{i}", encode_vector(vec), len(vec)),
        )
    conn.commit()
    return _cli_over(conn)


def _enriched_cli(tmp_path):
    """8 tracks, 6 enriched (ties resolve alphabetically: energetic / indie rock).

    taste_title -> 'energetic indie rock, with a post-punk undertow';
    I3 fires (melancholic + energetic); no sonic data anywhere.
    """
    embeddings = {f"artist{i}|||song{i}": [1.0, 0.01 * i] for i in range(8)}
    context = {
        f"artist{i}|||song{i}": _fj(
            genres="Indie Rock, Post-Punk",
            moods="Melancholic, Energetic",
            era="2020s",
        )
        for i in range(6)
    }
    return _build(tmp_path, embeddings, context=context)


class TestTasteMastheadAndFacets:
    def test_masthead_renders_computed_title_and_evidence(self, tmp_path, capsys):
        cli = _enriched_cli(tmp_path)
        payload = cli.show_taste(top=4)
        out = capsys.readouterr().out
        assert "Energetic Indie Rock, with a Post-Punk Undertow" in out
        assert "×6" in out  # evidence line carries tracks-tagged counts
        assert "(tracks tagged)" in _flat(out)
        assert payload["taste_title"] == "energetic indie rock, with a post-punk undertow"
        assert payload["context_coverage"] == {"with_context": 6, "seed": 8}

    def test_masthead_absent_without_context(self, tmp_path, capsys):
        cli = _seed(tmp_path)  # no track_context at all
        payload = cli.show_taste(top=2)
        out = capsys.readouterr().out
        assert "(tracks tagged)" not in out
        assert "Enriched views unlock after /enrich" in out  # ctx 0 < 5
        assert payload["taste_title"] is None

    def test_unlock_notice_below_five_context_tracks(self, tmp_path, capsys):
        embeddings = {f"a{i}|||s{i}": [1.0, 0.01 * i] for i in range(6)}
        context = {f"a{i}|||s{i}": _fj(genres="indie rock", moods="dreamy") for i in range(4)}
        cli = _build(tmp_path, embeddings, context=context)
        cli.show_taste(top=2)
        out = capsys.readouterr().out
        assert "Enriched views unlock after /enrich — 4/6 seed tracks have context." in out
        # The >= 5 contributing-track gate keeps the facet panels off too.
        assert "Moods you keep returning to" not in out
        assert "Genres in heavy rotation" not in out

    def test_facet_panels_and_era_render_when_gated_in(self, tmp_path, capsys):
        cli = _enriched_cli(tmp_path)
        payload = cli.show_taste(top=4)
        out = capsys.readouterr().out
        assert "Moods you keep returning to" in out
        assert "Genres in heavy rotation" in out
        assert "Era fingerprint" in out
        assert "6 of 8 datable from enriched era tags" in out
        assert payload["facets"]["moods"][0] == {"label": "energetic", "tracks": 6}
        assert payload["facets"]["genres"][0] == {"label": "indie rock", "tracks": 6}
        assert payload["decades"] == {
            "buckets": [{"decade": "2020s", "tracks": 6}],
            "datable": 6,
            "unbucketable": 0,
            "post_2010_pct": 100.0,
        }

    def test_tags_chips_from_stored_genre_order(self, tmp_path, capsys):
        cli = _enriched_cli(tmp_path)
        payload = cli.show_taste(top=8)
        rows = payload["most_representative"]
        enriched_rows = [r for r in rows if r["tags"]]
        assert enriched_rows, "expected enriched rows in the ranking"
        assert enriched_rows[0]["tags"] == ["indie rock", "post-punk"]
        # Context-less tracks render the honest dash, never a fake tag.
        bare = [r for r in rows if not r["tags"]]
        assert len(bare) == 2
        out = capsys.readouterr().out
        assert "—" in out


class TestTasteSonicColumn:
    def test_marker_column_present_with_sonic(self, tmp_path, capsys):
        cli = _seed(tmp_path, with_sonic=True)
        payload = cli.show_taste(top=2)
        out = _flat(capsys.readouterr().out)
        assert "●" in out
        assert "● sonic-informed (4/4)" in out
        assert "a ranking, not a match %" in out
        assert all(r["sonic_informed"] for r in payload["most_representative"])

    def test_marker_column_absent_without_sonic(self, tmp_path, capsys):
        cli = _seed(tmp_path)
        payload = cli.show_taste(top=2)
        out = _flat(capsys.readouterr().out)
        assert "●" not in out  # no dead glyphs: header and cells omitted
        assert "sonic-informed" not in out
        assert "a ranking, not a match %" in out  # the honesty caption stays
        assert all(not r["sonic_informed"] for r in payload["most_representative"])


def _crown_cli(tmp_path):
    """12 sonic tracks engineered for the crown rules.

    s0 maxes BOTH mood_happy (0.99) and mood_sad (0.95) -> dedupe sends
    SADDEST to s1 (0.90). danceability tops out at 0.55 (< 0.60 p-gate) and
    mood_acoustic is exactly 0.0 everywhere (missing-field degradation) ->
    both slots null. s3 is fastest (130 BPM), s4 slowest (85 BPM).
    """
    embeddings = {f"a{i}|||s{i}": [1.0, 0.01 * i] for i in range(12)}
    sonic = {}
    for i in range(12):
        vec = [0.1] * SONIC_DIM
        vec[1] = 0.0  # mood_acoustic: exact 0.0 never wins
        vec[11] = 0.45  # bpm_norm -> 121 BPM
        sonic[f"a{i}|||s{i}"] = vec
    sonic["a0|||s0"][4] = 0.99  # mood_happy
    sonic["a0|||s0"][7] = 0.95  # mood_sad (would win without dedupe)
    sonic["a1|||s1"][7] = 0.90  # mood_sad runner-up
    sonic["a2|||s2"][0] = 0.55  # danceability below the p-gate
    sonic["a3|||s3"][11] = 0.50  # 130 BPM
    sonic["a4|||s4"][11] = 0.25  # 85 BPM
    return _build(tmp_path, embeddings, sonic=sonic)


class TestTasteCrowns:
    def test_no_crowns_below_ten_sonic(self, tmp_path, capsys):
        cli = _seed(tmp_path, with_sonic=True)  # 4 sonic tracks
        payload = cli.show_taste(top=2)
        out = capsys.readouterr().out
        assert "Crowns" not in out
        assert "Your sound" in out  # no interference with the pinned section
        assert payload["superlatives"] is None

    def test_crowns_dedupe_pgate_and_zero_rule(self, tmp_path, capsys):
        cli = _crown_cli(tmp_path)
        payload = cli.show_taste(top=4)
        out = capsys.readouterr().out
        assert "Crowns · taste extremes" in out
        assert "HAPPIEST" in out
        assert "SADDEST" in out
        assert "MOST DANCEABLE" not in out  # p-gate at 0.60
        assert "MOST ACOUSTIC" not in out  # exact 0.0 never wins
        assert "classifier calls, not editorial ones" in _flat(out)
        crowns = payload["superlatives"]
        assert crowns["happiest"]["name"] == "S0"
        assert crowns["saddest"]["name"] == "S1"  # runner-up after dedupe
        assert crowns["most_danceable"] is None
        assert crowns["most_acoustic"] is None
        assert crowns["fastest"]["name"] == "S3"
        assert crowns["fastest"]["bpm"] == 130
        assert crowns["slowest"]["name"] == "S4"
        assert crowns["slowest"]["bpm"] == 85
        assert "tempo extremes:" in out
        assert payload["bpm_spread"] is not None  # 12 BPM readings >= 10


def _core_frontier_cli(tmp_path):
    """20 tracks in two separable groups: 15 'core' (indie rock /
    introspective / 2020s / 121 BPM) and 5 'frontier' (jazz / energetic /
    1990s / 139 BPM). Text and sonic channels both separate the groups, so
    the top quartile is all-core and the bottom quartile all-frontier."""
    embeddings = {}
    context = {}
    sonic = {}
    for i in range(15):
        tid = f"corea{i:02d}|||core{i:02d}"
        embeddings[tid] = [1.0, 0.001 * i]
        context[tid] = _fj(genres="indie rock", moods="introspective", era="2020s")
        vec = [0.1] * SONIC_DIM
        vec[0] = 0.9  # danceability-leaning sonic identity
        vec[11] = 0.45  # 121 BPM
        sonic[tid] = vec
    for i in range(5):
        tid = f"fronta{i:02d}|||front{i:02d}"
        embeddings[tid] = [0.001 * i, 1.0]
        context[tid] = _fj(genres="jazz", moods="energetic", era="1990s")
        vec = [0.1] * SONIC_DIM
        vec[2] = 0.9  # aggressive-leaning sonic identity
        vec[11] = 0.55  # 139 BPM
        sonic[tid] = vec
    return _build(tmp_path, embeddings, context=context, sonic=sonic)


class TestCoreVsFrontier:
    def test_absent_below_sixteen_ranked(self, tmp_path, capsys):
        cli = _seed(tmp_path)  # 4 tracks
        payload = cli.show_taste(top=2)
        out = capsys.readouterr().out
        assert "The core" not in out
        assert payload["core_vs_frontier"] is None

    def test_quartile_contrast_matches_hand_computed_aggregates(self, tmp_path, capsys):
        cli = _core_frontier_cli(tmp_path)
        payload = cli.show_taste(top=4)
        out = capsys.readouterr().out
        assert "The core" in out
        assert "The frontier" in out
        cvf = payload["core_vs_frontier"]
        assert cvf["core"] == {
            "genre": "indie rock",
            "mood": "introspective",
            "decade": "2020s",
            "bpm_median": 121,
            "sonic_tracks": 5,
        }
        assert cvf["frontier"] == {
            "genre": "jazz",
            "mood": "energetic",
            "decade": "1990s",
            "bpm_median": 139,
            "sonic_tracks": 5,
        }


class TestTasteInsights:
    def test_i3_mood_contrast_fires(self, tmp_path, capsys):
        cli = _enriched_cli(tmp_path)
        payload = cli.show_taste(top=4)
        out = capsys.readouterr().out
        line = "You keep melancholic (6 tracks) and energetic (6) side by side."
        assert line in payload["insights"]
        assert line in out
        assert "◆" in out

    def test_i1_and_i2_fire_on_split_quartiles(self, tmp_path):
        cli = _core_frontier_cli(tmp_path)
        payload = cli.show_taste(top=4)
        insights = payload["insights"]
        assert (
            "Your core leans indie rock / introspective; the frontier drifts toward "
            "jazz / energetic." in insights
        )
        assert any(insight.startswith("Split identity: 75% of your") for insight in insights)
        assert len(insights) <= 3

    def test_no_insights_without_signals(self, tmp_path):
        cli = _seed(tmp_path)  # no context, no sonic, 4 tracks
        payload = cli.show_taste(top=2)
        assert payload["insights"] == []


class TestTastePayloadAdditive:
    def test_old_keys_unchanged_and_new_keys_present(self, tmp_path):
        cli = _seed(tmp_path, with_sonic=True)
        payload = cli.show_taste(top=2)
        # Old contract, byte-identical semantics.
        assert payload["source"] == "your rotation"
        assert payload["enriched"] is False
        assert payload["built_from"] == 4
        assert payload["sonic_coverage"] == 4
        assert payload["sonic_profile"]["mood_relaxed"] == pytest.approx(0.8)
        assert {r["track_id"] for r in payload["most_representative"]} <= set(EMB)
        # New keys, all additive.
        assert payload["taste_title"] is None
        assert payload["context_coverage"] == {"with_context": 0, "seed": 4}
        assert payload["facets"] is None
        assert payload["decades"] is None
        assert payload["superlatives"] is None
        assert payload["core_vs_frontier"] is None
        assert payload["insights"] == []
        for row in payload["most_representative"]:
            assert row["sonic_informed"] is True
            assert row["tags"] == []

    def test_sparse_path_still_returns_none(self, tmp_path, capsys):
        db = Database(tmp_path / "sparse2.db")
        conn = db.connect()
        ensure_schema(conn)
        conn.commit()
        cli = _cli_over(conn)
        assert cli.show_taste() is None
        assert "Not enough embedded tracks" in capsys.readouterr().out
