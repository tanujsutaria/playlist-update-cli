"""Contract tests for `stats --json` and the /stats dashboard sections.

Covers: the new JSON payload (database/coverage/backfill/facets/decades/sonic/
playlist), JOIN-counted coverage (orphan side-table rows never inflate it),
export precedence over --json, mocked-`_db` degradation to the classic output,
and the playlist branch's generation hand-off strip + per-generation deltas.
Offline; no Spotify, no network, no embedding model.
"""

from __future__ import annotations

import argparse
import json
import re
from unittest.mock import MagicMock

import pytest

import ui
from main import PlaylistCLI, _identical_runs, dispatch_command
from song_store import SongStore
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.sonic import SONIC_FEATURES
from storage.vectors import encode_vector, vector_norm

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(out: str) -> str:
    """Strip ANSI codes and panel borders, then collapse whitespace, so phrases
    wrapped across console lines can still be matched as substrings."""
    out = _ANSI.sub("", out)
    out = re.sub(r"[│╭╮╰╯]", " ", out)
    return re.sub(r"\s+", " ", out)


@pytest.fixture(autouse=True)
def _reset_modes():
    ui.set_output_sink(None)
    ui.set_json_mode(False)
    yield
    ui.set_output_sink(None)
    ui.set_json_mode(False)


def _args(**kwargs):
    return argparse.Namespace(**kwargs)


def _stats_args(**overrides):
    base = {"playlist": None, "export": None, "output": None, "json": False}
    base.update(overrides)
    return _args(**base)


def _cli_over(conn) -> PlaylistCLI:
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    cli._db = SongStore(cli._repos, model_name="all-mpnet-base-v2")
    cli._spotify = object()
    cli._rotation_managers = {}
    return cli


def _fresh(tmp_path, name: str = "tunr.db"):
    db = Database(tmp_path / name)
    conn = db.connect()
    ensure_schema(conn)
    return conn


def _seed_library(conn):
    """4 tracks: 1 embedded, 1 enriched, 2 with spotify ids, 0 sonic — plus an
    ORPHAN track_context row (no tracks row) that JOIN-counting must exclude
    from coverage AND from the Library DNA facet counts."""
    conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'Artist')")
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status, spotify_id) "
        "VALUES (?, ?, 'a', 'candidate', ?)",
        [
            ("a|||t0", "t0", "sp0"),
            ("a|||t1", "t1", "sp1"),
            ("a|||t2", "t2", None),
            ("a|||t3", "t3", ""),  # empty string counts as missing
        ],
    )
    vec = [1.0, 0.0]
    conn.execute(
        "INSERT INTO track_embeddings "
        "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
        "VALUES ('a|||t0', 'm', ?, 2, ?)",
        (encode_vector(vec), vector_norm(vec)),
    )
    fields = json.dumps(
        [
            {"field": "genres", "value": "indie rock, dream pop"},
            {"field": "mood", "value": "dreamy"},
        ]
    )
    conn.execute(
        "INSERT INTO track_context (track_id, fields_json) VALUES ('a|||t0', ?)", (fields,)
    )
    conn.commit()
    # The orphan: a context row whose track was deleted. FK pragma must be
    # toggled off to plant it (the schema enforces the reference).
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO track_context (track_id, fields_json) VALUES ('ghost|||gone', ?)", (fields,)
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")


def _seed_enriched(conn, *, eras):
    """len(eras) tracks, each enriched with genres/moods and its era value —
    enough contributing tracks (>= 5) to light up the Library DNA section."""
    conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'Artist')")
    for index, era in enumerate(eras):
        tid = f"a|||t{index}"
        conn.execute(
            "INSERT INTO tracks (track_id, name, artist_id, status) "
            "VALUES (?, ?, 'a', 'candidate')",
            (tid, f"t{index}"),
        )
        fields = json.dumps(
            [
                {"field": "genres", "value": "indie rock"},
                {"field": "moods", "value": "dreamy"},
                {"field": "era", "value": era},
            ]
        )
        conn.execute(
            "INSERT INTO track_context (track_id, fields_json) VALUES (?, ?)", (tid, fields)
        )
    conn.commit()


class TestStatsJsonContract:
    def test_payload_shape_and_join_counted_coverage(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        _seed_library(conn)
        cli = _cli_over(conn)
        rc = dispatch_command(cli, "stats", _stats_args(json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        # `database` mirrors get_stats() verbatim (same source as --export).
        assert payload["database"] == cli.db.get_stats()
        assert payload["database"]["total_songs"] == 4
        # JOIN-counted: the orphan context row does NOT inflate coverage.
        assert payload["coverage"] == {
            "embeddings": {"have": 1, "total": 4},
            "context": {"have": 1, "total": 4},
            "sonic": {"have": 0, "total": 4},
            "spotify_id": {"have": 2, "total": 4},
        }
        assert payload["backfill"] == {
            "missing_embeddings": 3,
            "missing_context": 3,
            "missing_sonic": 4,
            "missing_spotify_id": 2,
        }
        # Context rows exist -> facets present. The orphan's tags do NOT count:
        # facets are JOIN-scoped to live tracks, same doctrine as coverage.
        # No era values -> decades null; no sonic rows -> sonic null.
        assert payload["facets"] is not None
        assert {"label": "indie rock", "tracks": 1} in payload["facets"]["genres"]
        assert payload["decades"] is None
        assert payload["sonic"] is None
        assert payload["playlist"] is None

    def test_facets_decades_sonic_null_on_empty_side_tables(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'Artist')")
        conn.execute(
            "INSERT INTO tracks (track_id, name, artist_id, status) "
            "VALUES ('a|||t0', 't0', 'a', 'candidate')"
        )
        conn.commit()
        cli = _cli_over(conn)
        rc = dispatch_command(cli, "stats", _stats_args(json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["facets"] is None
        assert payload["decades"] is None
        assert payload["sonic"] is None
        assert payload["coverage"]["embeddings"] == {"have": 0, "total": 1}

    def test_empty_library_renders_notice_and_zeroed_payload(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        cli = _cli_over(conn)
        payload = cli.show_stats()
        out = _flat(capsys.readouterr().out)
        assert "Library is empty — /ingest or /search to begin." in out
        assert "Data Coverage" not in out
        assert payload["coverage"]["embeddings"] == {"have": 0, "total": 0}


class TestStatsDashboardRendering:
    def test_coverage_panel_and_backfill_queue_render(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        _seed_library(conn)
        cli = _cli_over(conn)
        cli.show_stats()
        out = _flat(capsys.readouterr().out)
        assert "Database Stats" in out  # the pinned classic table stays
        assert "Data Coverage" in out
        assert "1/4" in out  # embeddings coverage
        assert "grey = not there yet · sonic comes from AcousticBrainz lookups" in out
        assert "Backfill queue" in out
        assert "embeddings missing" in out
        assert "no AcousticBrainz match; never inferred" in out

    def test_sound_section_gated_below_ten_sonic_rows(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        _seed_library(conn)
        cli = _cli_over(conn)
        cli.show_stats()
        out = capsys.readouterr().out
        assert "The sound, measured" not in out

    def test_library_dna_names_enrichment_provenance(self, tmp_path, capsys):
        """The populated facet panels must say where the tags come from —
        /enrich web context, never (implied) Spotify metadata or audio
        analysis. /taste already does; /stats must match."""
        conn = _fresh(tmp_path)
        _seed_enriched(conn, eras=["2020s"] * 6)
        cli = _cli_over(conn)
        cli.show_stats()
        out = _flat(capsys.readouterr().out)
        assert "Library DNA" in out
        assert "tags from /enrich web context — semantic, not acoustic" in out

    def test_era_caption_examples_quoted_apostrophe_proof(self, tmp_path, capsys):
        """An era value can BEGIN with an apostrophe ("'60s pop") — straight
        quotes would render a doubled ''60s pop'. Curly quotes cannot collide."""
        conn = _fresh(tmp_path)
        _seed_enriched(conn, eras=["2020s"] * 5 + ["'60s pop"])
        cli = _cli_over(conn)
        payload = cli.show_stats()
        out = _flat(capsys.readouterr().out)
        assert "5/6 enriched tracks datable · 1 defied parsing (“'60s pop”, ...)" in out
        assert "''60s pop'" not in out
        assert payload["decades"]["unbucketable"] == 1

    def test_era_caption_examples_dropped_when_over_budget(self, tmp_path, capsys):
        """The examples are illustrative only: when they would push the caption
        past footnote size, the counts stay and the parenthetical goes."""
        conn = _fresh(tmp_path)
        long_era = "'60s jangle pop psychedelia revivalism"
        _seed_enriched(conn, eras=["2020s"] * 5 + [long_era])
        cli = _cli_over(conn)
        cli.show_stats()
        out = _flat(capsys.readouterr().out)
        assert "5/6 enriched tracks datable · 1 defied parsing" in out
        assert "jangle" not in out  # the over-budget example never renders

    def test_sound_section_renders_with_sonic_rows(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'Artist')")
        bpm_idx = SONIC_FEATURES.index("bpm_norm")
        for index in range(12):
            tid = f"a|||t{index}"
            conn.execute(
                "INSERT INTO tracks (track_id, name, artist_id, status) "
                "VALUES (?, ?, 'a', 'candidate')",
                (tid, f"t{index}"),
            )
            vec = [0.0] * len(SONIC_FEATURES)
            vec[bpm_idx] = (120.0 - 40.0) / 180.0  # every track at 120 BPM
            conn.execute(
                "INSERT INTO track_sonic (track_id, sonic_blob, sonic_dim, features_json) "
                "VALUES (?, ?, ?, ?)",
                (tid, encode_vector(vec), len(vec), json.dumps({"key": "A major"})),
            )
        conn.commit()
        cli = _cli_over(conn)
        payload = cli.show_stats()
        out = _flat(capsys.readouterr().out)
        assert "The sound, measured" in out
        assert "Tempo distribution · 12/12 tracks (AcousticBrainz)" in out
        assert "median 120 BPM" in out
        assert "Common keys: A major (12)" in out
        assert payload["sonic"] == {
            "tracks": 12,
            "bpm_tracks": 12,
            "bpm_histogram": [{"bucket": "120-139", "tracks": 12}],
            "bpm_median": 120,
            "keys": [{"key": "A major", "tracks": 12}],
        }

    def test_tempo_title_excludes_tracks_without_ab_tempo(self, tmp_path, capsys):
        """A sonic row with bpm_norm == 0.0 (AB had no tempo) must not be
        claimed by the histogram title — the chart never bucketed it."""
        conn = _fresh(tmp_path)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'Artist')")
        bpm_idx = SONIC_FEATURES.index("bpm_norm")
        for index in range(14):
            tid = f"a|||t{index}"
            conn.execute(
                "INSERT INTO tracks (track_id, name, artist_id, status) "
                "VALUES (?, ?, 'a', 'candidate')",
                (tid, f"t{index}"),
            )
            vec = [0.0] * len(SONIC_FEATURES)
            if index < 12:
                vec[bpm_idx] = (120.0 - 40.0) / 180.0  # 120 BPM
            # the last 2 keep bpm_norm == 0.0: AB had no tempo for them
            conn.execute(
                "INSERT INTO track_sonic (track_id, sonic_blob, sonic_dim) VALUES (?, ?, ?)",
                (tid, encode_vector(vec), len(vec)),
            )
        conn.commit()
        cli = _cli_over(conn)
        payload = cli.show_stats()
        out = _flat(capsys.readouterr().out)
        assert "Tempo distribution · 12/14 tracks (AcousticBrainz)" in out
        assert "2 tracks without AB tempo excluded" in out
        assert payload["sonic"]["tracks"] == 14
        assert payload["sonic"]["bpm_tracks"] == 12
        assert sum(b["tracks"] for b in payload["sonic"]["bpm_histogram"]) == 12


class TestExportPrecedence:
    def test_export_wins_over_json(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        _seed_library(conn)
        cli = _cli_over(conn)
        out_file = tmp_path / "stats.json"
        rc = dispatch_command(
            cli, "stats", _stats_args(json=True, export="json", output=str(out_file))
        )
        assert rc == 0
        assert out_file.exists()
        out = capsys.readouterr().out
        assert "--json ignored with --export" in out
        assert not out.lstrip().startswith("{")  # stdout is NOT a JSON payload


class TestMockedRepoDegradation:
    def test_mocked_db_degrades_to_classic_output(self, capsys):
        cli = PlaylistCLI.__new__(PlaylistCLI)
        cli._db = MagicMock()
        cli._db.get_stats.return_value = {
            "total_songs": 7,
            "embedding_dimensions": 768,
            "storage_size_mb": 1.5,
        }
        # No `_repos` injected: the extras must degrade silently, never lazily
        # opening a database from inside a stats render.
        rc = dispatch_command(cli, "stats", _stats_args())
        assert rc == 0
        cli._db.get_stats.assert_called_once()
        out = capsys.readouterr().out
        assert "Database Stats" in out
        assert "Data Coverage" not in out
        assert "Backfill queue" not in out


def _seed_playlist(conn, *, batch_timestamps: bool):
    """'mix' with 4 generations: [a,b] -> [c,d] -> [c,d] (identical) -> [e,f]."""
    conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'Artist')")
    track_ids = [f"a|||t{i}" for i in range(6)]
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, 'a', 'candidate')",
        [(tid, f"t{index}") for index, tid in enumerate(track_ids)],
    )
    conn.execute(
        "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('mix', 'mix', 4)"
    )
    stamps = (
        ["2026-01-01T00:00:00"] * 4
        if batch_timestamps
        else [f"2026-01-0{i}T00:00:00" for i in range(1, 5)]
    )
    conn.executemany(
        "INSERT INTO rotation_generations "
        "(generation_id, playlist_id, generation_index, created_at) VALUES (?, 'mix', ?, ?)",
        [(f"g{i}", i, stamps[i]) for i in range(4)],
    )
    generations = [
        [track_ids[0], track_ids[1]],
        [track_ids[2], track_ids[3]],
        [track_ids[2], track_ids[3]],
        [track_ids[4], track_ids[5]],
    ]
    rows = []
    for gi, gen in enumerate(generations):
        for pos, tid in enumerate(gen):
            rows.append((f"g{gi}", tid, pos))
    conn.executemany(
        "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()


def _seed_many_generations(conn, count: int):
    """`count` single-track generations alternating two tracks (all hand-offs
    are full refreshes), with distinct timestamps — for the strip window."""
    conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'Artist')")
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, 'a', 'candidate')",
        [("a|||t0", "t0"), ("a|||t1", "t1")],
    )
    conn.execute(
        "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('mix', 'mix', ?)",
        (count,),
    )
    conn.executemany(
        "INSERT INTO rotation_generations "
        "(generation_id, playlist_id, generation_index, created_at) VALUES (?, 'mix', ?, ?)",
        [(f"g{i}", i, f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00") for i in range(count)],
    )
    conn.executemany(
        "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES (?, ?, 0)",
        [(f"g{i}", f"a|||t{i % 2}") for i in range(count)],
    )
    conn.commit()


class TestPlaylistBranch:
    def test_handoff_strip_findings_and_deltas(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        _seed_playlist(conn, batch_timestamps=True)
        cli = _cli_over(conn)
        cli.show_stats("mix")
        out = _flat(capsys.readouterr().out)
        assert "Playlist Stats" in out  # pinned section intact
        assert "Generation hand-off" in out
        assert "cold = 0 shared · bright = identical sets" in out
        assert "generations 2–3 contain identical track sets (migration artifact)" in out
        assert "every other hand-off is a full refresh" in out
        assert "6 distinct tracks filled 8 slots" in out
        assert "generation timestamps were backfilled in one batch" in out
        # Newest generation first, the duplicated hand-off honestly shows 0 new.
        assert out.index("Generation 4") < out.index("Generation 3")
        assert "Generation 3 · 2 tracks · 0 new vs previous" in out
        assert "Generation 4 · 2 tracks · 2 new vs previous" in out

    def test_no_timestamp_note_with_distinct_stamps(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        _seed_playlist(conn, batch_timestamps=False)
        cli = _cli_over(conn)
        cli.show_stats("mix")
        out = _flat(capsys.readouterr().out)
        assert "Generation hand-off" in out
        assert "backfilled in one batch" not in out
        assert "(migration artifact)" not in out

    def test_playlist_json_payload(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        _seed_playlist(conn, batch_timestamps=True)
        cli = _cli_over(conn)
        rc = dispatch_command(cli, "stats", _stats_args(playlist="mix", json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        pl = payload["playlist"]
        # Export-shaped keys preserved.
        assert pl["name"] == "mix"
        assert pl["total_songs"] == 6
        assert pl["unique_songs_used"] == 6
        assert pl["generations_count"] == 4
        assert pl["complete_rotation_achieved"] is True
        # Additive keys.
        assert pl["consecutive_overlap"] == [0.0, 1.0, 0.0]
        assert pl["distinct_rotated"] == 6
        assert pl["recent_generations"] == [
            {"index": 4, "size": 2, "new_vs_previous": 2},
            {"index": 3, "size": 2, "new_vs_previous": 0},
            {"index": 2, "size": 2, "new_vs_previous": 2},
            {"index": 1, "size": 2, "new_vs_previous": None},
        ]

    def test_strip_windowed_to_console_with_window_named(self, tmp_path, capsys):
        """The strip grows 2 cells per generation: beyond the console it is
        windowed to the most recent hand-offs (never wrapped mid-heatmap) and
        the legend — on its own line — names the window."""
        conn = _fresh(tmp_path)
        _seed_many_generations(conn, count=60)
        cli = _cli_over(conn)
        cli.show_stats("mix")
        out = _flat(capsys.readouterr().out)
        assert "Generation hand-off" in out
        window = max(1, (ui.console.options.max_width - 4) // 2)
        if window < 59:  # 59 hand-offs cannot fit this console
            assert f"last {window} hand-offs ({60 - window}→60)" in out
        else:  # console wide enough for the whole strip — no window note
            assert "hand-offs (" not in out
        assert "cold = 0 shared · bright = identical sets" in out
        assert "every other hand-off is a full refresh" not in out  # no 1.0 runs

    def test_strip_skipped_with_single_generation(self, tmp_path, capsys):
        conn = _fresh(tmp_path)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'Artist')")
        conn.execute(
            "INSERT INTO tracks (track_id, name, artist_id, status) "
            "VALUES ('a|||t0', 't0', 'a', 'candidate')"
        )
        conn.execute(
            "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('mix', 'mix', 1)"
        )
        conn.execute(
            "INSERT INTO rotation_generations (generation_id, playlist_id, generation_index) "
            "VALUES ('g0', 'mix', 0)"
        )
        conn.execute(
            "INSERT INTO generation_tracks (generation_id, track_id, position) "
            "VALUES ('g0', 'a|||t0', 0)"
        )
        conn.commit()
        cli = _cli_over(conn)
        cli.show_stats("mix")
        out = _flat(capsys.readouterr().out)
        assert "Playlist Stats" in out
        assert "Generation hand-off" not in out
        assert "Generation 1 · 1 track" in out


class TestIdenticalRuns:
    def test_single_run_maps_to_generation_range(self):
        assert _identical_runs([0.0, 1.0, 0.0]) == [(2, 3)]

    def test_run_of_two_handoffs_spans_three_generations(self):
        # Live-DB shape: overlaps[2] and overlaps[3] are 1.0 -> gens 3-5.
        overlaps = [0.0, 0.0, 1.0, 1.0, 0.0]
        assert _identical_runs(overlaps) == [(3, 5)]

    def test_trailing_run_reaches_last_generation(self):
        assert _identical_runs([0.0, 1.0]) == [(2, 3)]

    def test_no_runs(self):
        assert _identical_runs([0.0, 0.5, 0.0]) == []
