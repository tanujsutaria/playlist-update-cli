"""pytest-bdd bindings for the ``rotation_update.feature`` scenarios.

Exercises the rotation/update flow end to end against the shared foundation: a
real SQLite-backed ``SongStore`` (seeded via ``seeded_repos``), a fully-wired
``PlaylistCLI``, the deterministic ``FakeSpotify`` double, and a real
``RotationManager`` (built by ``cli._get_rotation_manager`` with the wired
``repos``). The ``update`` command is dispatched through the real
``arg_parse.parse_tokens`` + ``dispatch_command`` via the ``run`` helper.

What is asserted (real behavior, not trivial truths):
  * Dry run: return code 0, the selected songs are printed, and NEITHER the
    rotation tables NOR Spotify were touched.
  * Real update: a new ``rotation_generations`` row and new
    ``generation_tracks`` rows are persisted to SQLite, the playlist's
    ``current_generation`` advances, and ``FakeSpotify.refresh_playlist`` is
    recorded.
  * Selection priority: with the whole seeded library already used across the
    two rotation generations, two freshly inserted (never-used) songs are the
    ones selected.

Integration note (for the reconcile phase):
  ``RotationManager._save_history`` derives each generation id as
  ``uuid5(NAMESPACE_URL, f"{slug}|{index}").hex``. The shared ``seeded_repos``
  fixture seeds the "Favorites" playlist with generation ids of the form
  ``favorites-gen-N`` instead, so a real ``update_playlist`` on "Favorites"
  raises a (swallowed) ``FOREIGN KEY constraint failed`` when ``_save_history``
  tries to attach tracks to a re-derived uuid5 generation id that was never
  inserted. To assert genuine persistence growth this module seeds a separate,
  rotation-compatible playlist ("Mixtape") whose generation ids match the
  uuid5 scheme. If a later phase wants the seeded "Favorites" generations to be
  directly update-compatible, switch the conftest seed to the uuid5 ids (or fix
  ``_save_history`` to use ``upsert``'s returned id when adding tracks).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from models import track_id_for
from storage.vectors import encode_vector, vector_norm

# Feature file resolved relative to bdd_features_base_dir (tests/bdd/features).
scenarios("rotation_update.feature")


def _slug(name: str) -> str:
    """Mirror ``rotation_manager._playlist_slug`` for deriving playlist keys."""
    return re.sub(r"[^a-z0-9_-]", "_", name.lower())


def _gen_id(slug: str, index: int) -> str:
    """Generation id exactly as ``RotationManager._save_history`` derives it."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}|{index}").hex


def _count_generation_tracks(repos, slug: str) -> int:
    row = repos.conn.execute(
        """
        SELECT COUNT(*) FROM generation_tracks
        WHERE generation_id IN (
            SELECT generation_id FROM rotation_generations WHERE playlist_id = ?
        );
        """,
        (slug,),
    ).fetchone()
    return int(row[0])


def _seed_track(repos, store, artist: str, name: str) -> str:
    """Insert a brand-new artist/track (+ offline embedding); return its id."""
    now = datetime.now().isoformat()
    artist_id = artist.lower()
    track_id = track_id_for(artist, name)
    uri = f"spotify:track:{track_id.replace('|', '').replace(' ', '')[:22]}"

    repos.artists.upsert(artist_id=artist_id, name=artist, genres_json="[]", updated_at=now)
    repos.tracks.upsert(
        {
            "track_id": track_id,
            "spotify_id": uri,
            "name": name,
            "artist_id": artist_id,
            "album_name": None,
            "release_date": None,
            "duration_ms": None,
            "explicit": None,
            "popularity": None,
            "spotify_url": None,
            "status": "candidate",
            "last_decision": None,
            "decision_reason": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    vector = store._embedder().embed([f"{name} by {artist}"])[0]
    repos.embeddings.upsert(
        {
            "track_id": track_id,
            "model_name": store.model_name,
            "embedding_blob": encode_vector(vector),
            "embedding_dim": len(vector),
            "embedding_norm": vector_norm(vector),
            "strict_ratio": None,
            "created_at": now,
        }
    )
    return track_id


@pytest.fixture
def state() -> dict:
    """Mutable per-scenario bag for passing values between steps."""
    return {}


# --------------------------------------------------------------------------- #
# Background
# --------------------------------------------------------------------------- #
@given("a seeded library")
def _seeded_library(seeded_repos, state):
    # seeded_repos materializes the temp DB + seed data; cli/run depend on it.
    state["seeded"] = seeded_repos


# --------------------------------------------------------------------------- #
# Scenario: Dry run previews a selection without writing anything
# --------------------------------------------------------------------------- #
@when(parsers.parse('I run the update command "{command}"'))
def _run_update(run, state, capsys, command):
    seeded = state["seeded"]
    slug = seeded.playlist_slug
    state["gens_before"] = len(seeded.repos.rotation_generations.list_by_playlist(slug))
    state["gtracks_before"] = _count_generation_tracks(seeded.repos, slug)
    state["rc"] = run(command)
    state["out"] = capsys.readouterr().out


@then(parsers.parse("it exits with code {code:d}"))
def _exits_with(code, state):
    assert state["rc"] == code


@then("the dry-run output lists the selected songs")
def _dry_run_lists_songs(state):
    out = state["out"]
    # The dry-run branch prints a "Dry Run / Selected Songs" section + total.
    assert "Selected" in out
    assert "Total selected:" in out


@then("no rotation generation rows were added")
def _no_generation_rows_added(state):
    seeded = state["seeded"]
    slug = seeded.playlist_slug
    gens_after = len(seeded.repos.rotation_generations.list_by_playlist(slug))
    gtracks_after = _count_generation_tracks(seeded.repos, slug)
    assert gens_after == state["gens_before"]
    assert gtracks_after == state["gtracks_before"]


@then("the playlist was not refreshed on Spotify")
def _spotify_not_refreshed(cli, state):
    assert cli.spotify.refresh_calls == []


# --------------------------------------------------------------------------- #
# Scenario: A real update records a new rotation generation
# --------------------------------------------------------------------------- #
@given(parsers.parse('a rotation-compatible playlist "{name}" with {n:d} generations'))
def _compat_playlist(seeded_repos, state, name, n):
    repos = seeded_repos.repos
    slug = _slug(name)
    now = datetime.now().isoformat()
    # current_generation = n - 1 (newest existing generation index), matching
    # the seed convention used for "Favorites".
    repos.playlists.upsert(
        playlist_id=slug,
        name=name,
        current_generation=n - 1,
        created_at=now,
        updated_at=now,
    )
    # Generation ids MUST follow the uuid5 scheme that _save_history derives,
    # otherwise the next real update hits a FOREIGN KEY error (see module docs).
    tracks = seeded_repos.track_ids
    layouts = [tracks[i : i + 2] for i in range(n)]
    for idx in range(n):
        gid = _gen_id(slug, idx)
        repos.rotation_generations.upsert(gid, slug, idx, created_at=now)
        for pos, track_id in enumerate(layouts[idx]):
            repos.generation_tracks.add(gid, track_id, pos)
    repos.conn.commit()

    state["compat_name"] = name
    state["compat_slug"] = slug
    state["gens_before"] = len(repos.rotation_generations.list_by_playlist(slug))
    state["gtracks_before"] = _count_generation_tracks(repos, slug)
    state["curgen_before"] = repos.playlists.get(slug)["current_generation"]


@when(parsers.parse('I run a real update of "{name}" requesting {count:d} songs'))
def _run_real_update(run, state, name, count):
    state["rc"] = run(f"update {name} --count {count}")


@then(parsers.parse('a new rotation generation row was added for "{name}"'))
def _generation_row_added(seeded_repos, state, name):
    slug = _slug(name)
    gens_after = len(seeded_repos.repos.rotation_generations.list_by_playlist(slug))
    assert gens_after == state["gens_before"] + 1


@then(parsers.parse('new generation_tracks rows were recorded for "{name}"'))
def _generation_tracks_added(seeded_repos, state, name):
    slug = _slug(name)
    gtracks_after = _count_generation_tracks(seeded_repos.repos, slug)
    assert gtracks_after > state["gtracks_before"]


@then("the playlist current generation advanced by one")
def _current_generation_advanced(seeded_repos, state):
    slug = state["compat_slug"]
    curgen_after = seeded_repos.repos.playlists.get(slug)["current_generation"]
    assert curgen_after == state["curgen_before"] + 1


@then("the playlist was refreshed on Spotify")
def _spotify_refreshed(cli, state):
    calls = cli.spotify.refresh_calls
    assert len(calls) == 1
    assert calls[0]["name"] == state["compat_name"]
    assert len(calls[0]["songs"]) > 0


# --------------------------------------------------------------------------- #
# Scenario: Selection prefers previously-unused songs
# --------------------------------------------------------------------------- #
@given("two brand-new songs that have never been in any rotation")
def _two_new_songs(seeded_repos, state):
    repos = seeded_repos.repos
    store = seeded_repos.store
    new_ids = [
        _seed_track(repos, store, "Zeta Crew", "Sixth Sense"),
        _seed_track(repos, store, "Eta Squad", "Seventh Heaven"),
    ]
    repos.conn.commit()
    state["new_ids"] = set(new_ids)


@when(parsers.parse('I select {count:d} songs for the next rotation of "{name}"'))
def _select_for_next_rotation(cli, state, count, name):
    rm = cli._get_rotation_manager(name)
    state["selected"] = rm.select_songs_for_today(count=count)


@then("the selection consists only of the brand-new songs")
def _selection_is_only_new(state):
    selected_ids = {s.id for s in state["selected"]}
    # The whole seeded "Favorites" library is already used across both seeded
    # generations, so the only never-used songs are the two we just added.
    assert selected_ids == state["new_ids"]
    assert len(state["selected"]) == 2
