"""Command registry and dispatch — extracted verbatim from main.py (PR 4).

Each command is handled by a small module-level function that pulls the
relevant fields off the parsed argparse Namespace and delegates to a
PlaylistCLI method, returning an exit code (0 success, 1 error). The
`_COMMAND_HANDLERS` mapping (defined below the handlers) replaces what used
to be a ~200-line if/elif ladder. The public contract
(`dispatch_command(cli, command, args) -> int`, the cli method signatures,
and the return codes) is unchanged and locked by tests/test_dispatch_command.py.

main.py re-exports `dispatch_command` and `_COMMAND_HANDLERS`; the registry
must remain the SAME dict object in both modules — tests mutate it via
monkeypatch.setitem — pinned by tests/test_registry_parser_sync.py.

NOTE (future step): per-domain vertical extraction (search / rotation /
ingest / maintenance / auth) moves the thick handler bodies and their cli
methods together; this module then shrinks to thin delegates + the registry.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import doctor
from commands.debug import _present_debug
from doctor import DEFAULT_EMBEDDING_MODEL, has_failures
from doctor import results_payload as doctor_results_payload
from doctor import run_checks as run_doctor_checks
from gdpr_import import GdprImportError, import_streaming_history, iter_streaming_records
from ui import (
    ColumnSpec,
    caption,
    emit_json,
    error,
    info,
    json_output,
    json_payload,
    link_text,
    notice,
    section,
    set_json_mode,
    summary_panel,
    table,
    warning,
)

if TYPE_CHECKING:
    from main import PlaylistCLI

logger = logging.getLogger(__name__)


def _handle_import(cli: "PlaylistCLI", args: Any) -> int:
    cli.import_songs(args.file)
    return 0


def _handle_update(cli: "PlaylistCLI", args: Any) -> int:
    cli.update_playlist(
        args.playlist,
        args.count,
        args.fresh_days,
        args.dry_run,
        args.score_strategy,
        args.query,
    )
    return 0


def _handle_stats(cli: "PlaylistCLI", args: Any) -> int:
    if hasattr(args, "output") and args.output and not args.export:
        logger.warning("--output requires --export; ignoring --output")
    json_mode = getattr(args, "json", False)
    if args.export:
        # Export wins: the file formats are the stable machine contract here.
        if json_mode:
            warning("--json ignored with --export")
        cli.export_stats(args.playlist, args.export, args.output)
        return 0
    with json_payload(json_mode) as out:
        out["payload"] = cli.show_stats(args.playlist)
    return 0


def _handle_status(cli: "PlaylistCLI", args: Any) -> int:
    cli.show_status()
    return 0


def _handle_profile(cli: "PlaylistCLI", args: Any) -> int:
    with json_payload(getattr(args, "json", False)) as out:
        out["payload"] = cli.show_profile(args.top)
    return 0


def _handle_taste(cli: "PlaylistCLI", args: Any) -> int:
    with json_payload(getattr(args, "json", False)) as out:
        out["payload"] = cli.show_taste(args.top)
    return 0


def _handle_view(cli: "PlaylistCLI", args: Any) -> int:
    cli.view_playlist(args.playlist)
    return 0


def _handle_sync(cli: "PlaylistCLI", args: Any) -> int:
    cli.sync_playlist(args.playlist)
    return 0


def _handle_extract(cli: "PlaylistCLI", args: Any) -> int:
    cli.extract_playlist(args.playlist, args.output)
    return 0


def _handle_plan(cli: "PlaylistCLI", args: Any) -> int:
    cli.plan_playlist(
        args.playlist,
        args.count,
        args.fresh_days,
        args.generations,
        args.score_strategy,
        args.query,
    )
    return 0


def _handle_diff(cli: "PlaylistCLI", args: Any) -> int:
    cli.diff_playlist(args.playlist, args.count, args.fresh_days, args.score_strategy, args.query)
    return 0


def _handle_clean(cli: "PlaylistCLI", args: Any) -> int:
    cli.clean_database(args.dry_run)
    return 0


def _handle_search(cli: "PlaylistCLI", args: Any) -> int:
    json_mode = getattr(args, "json", False)
    set_json_mode(json_mode)
    try:
        cli.search_songs(args.query)
        track_ids = cli.last_search_track_ids or []
        handled = False
        if track_ids:
            if getattr(args, "save", False):
                cli.mark_search_tracks(track_ids, status="accepted")
                info(f"Marked {len(track_ids)} result(s) as accepted.")
                handled = True
            to_playlist = getattr(args, "to_playlist", None)
            if to_playlist:
                limit = getattr(args, "limit", None)
                chosen = track_ids[:limit] if limit else track_ids
                cli.add_search_to_playlist(
                    to_playlist, chosen, replace=getattr(args, "replace", False)
                )
                handled = True
            # Tell the interactive UI the results are already dealt with, so it
            # doesn't also pop the yes/no -> db/playlist -> name follow-up prompts.
            if handled:
                cli.last_search_handled = True
    finally:
        if json_mode:
            emit_json(
                {
                    "query": cli.last_search_query,
                    "count": len(cli.last_search_results or []),
                    "results": cli.last_search_results or [],
                }
            )
        set_json_mode(False)
    return 0


def _handle_find(cli: "PlaylistCLI", args: Any) -> int:
    """Flagship: deep search, re-ranked by taste, optionally written to a playlist.

    Composes search_songs (#search) -> taste_rank_last_search (taste centroid) ->
    add_search_to_playlist (guarded, undoable). The intermediate search rendering
    is suppressed so /find shows only the re-ranked view.
    """
    want_json = getattr(args, "json", False)
    weight = max(0.0, min(1.0, getattr(args, "taste_weight", 0.5)))
    to_playlist = getattr(args, "to_playlist", None)
    limit = getattr(args, "limit", None)
    replace = getattr(args, "replace", False)

    # Run the search quietly — /find presents the re-ranked list, not the raw search.
    set_json_mode(True)
    ranked: List[Dict[str, Any]] = []
    signal = ""
    try:
        cli.search_songs(args.query)
        ranked, signal = cli.taste_rank_last_search(taste_weight=weight)
    finally:
        if not want_json:
            set_json_mode(False)
    # Persist the taste-ranked order (search_songs above just cleared it):
    # /results uses this so the browser mirrors the table /find rendered.
    cli.last_find_ranked = ranked or None

    def _chosen_ids() -> List[str]:
        ids = [r["track_id"] for r in ranked if r.get("track_id")]
        return ids[:limit] if limit else ids

    if want_json:
        wrote = None
        if to_playlist and ranked:
            chosen = _chosen_ids()
            ok = cli.add_search_to_playlist(to_playlist, chosen, replace=replace)
            cli.last_search_handled = True
            wrote = {"playlist": to_playlist, "requested": len(chosen), "ok": bool(ok)}
        emit_json(
            {
                "query": cli.last_search_query,
                "taste_weight": weight,
                "signal": signal,
                "count": len(ranked),
                "results": ranked,
                "wrote": wrote,
            }
        )
        set_json_mode(False)
        return 0

    if not ranked:
        notice("No results to rank.")
        return 0
    section("Find", cli.last_search_query)
    info(f"Blend: {round(weight * 100)}% taste · {round((1 - weight) * 100)}% relevance — {signal}")
    table(
        [
            ColumnSpec("#", justify="right", style="dim"),
            "Song",
            "Artist",
            "Year",
            ColumnSpec("Rel", metric=True),
            ColumnSpec("Taste", metric=True),
            ColumnSpec("Blend", metric=True),
        ],
        [
            [
                i,
                r["song"],
                r["artist"],
                r["year"] or "-",
                f"{r['rel_norm']:.2f}",
                f"{r['taste_norm']:.2f}",
                f"{r['blended']:.2f}",
            ]
            for i, r in enumerate(ranked, 1)
        ],
    )
    if to_playlist:
        cli.add_search_to_playlist(to_playlist, _chosen_ids(), replace=replace)
        cli.last_search_handled = True
    else:
        info("Preview only. Re-run with --to NAME to add these to a playlist.")
    return 0


def _handle_undo(cli: "PlaylistCLI", args: Any) -> int:
    cli.undo_last_write()
    return 0


def _cohort_from_args(args: Any) -> Tuple[Optional[str], Optional[str]]:
    """Map the mutually-exclusive cohort flags to (cohort, playlist_name).

    argparse enforces the mutual exclusion; no flag means whole-library (None).
    """
    if getattr(args, "played", False):
        return "played", None
    if getattr(args, "liked", False):
        return "liked", None
    if getattr(args, "rotation", False):
        return "rotation", None
    playlist = getattr(args, "playlist", None)
    if playlist:
        return "playlist", playlist
    return None, None


def _handle_enrich(cli: "PlaylistCLI", args: Any) -> int:
    cohort, playlist = _cohort_from_args(args)
    cli.enrich_library(
        limit=getattr(args, "limit", 25),
        dry_run=getattr(args, "dry_run", False),
        concurrency=getattr(args, "concurrency", 8),
        cohort=cohort,
        playlist=playlist,
    )
    return 0


def _handle_sonic(cli: "PlaylistCLI", args: Any) -> int:
    cohort, playlist = _cohort_from_args(args)
    cli.sonic_backfill(
        limit=getattr(args, "limit", 50),
        dry_run=getattr(args, "dry_run", False),
        cohort=cohort,
        playlist=playlist,
    )
    return 0


def _handle_embed(cli: "PlaylistCLI", args: Any) -> int:
    cli.embed_backfill(limit=getattr(args, "limit", None), dry_run=getattr(args, "dry_run", False))
    return 0


def _handle_similar(cli: "PlaylistCLI", args: Any) -> int:
    """Local more-like-this over stored embeddings — offline and instant."""
    with json_payload(getattr(args, "json", False)) as out:
        out["payload"] = {}
        query = " ".join(args.query)
        payload = out["payload"] = cli.similar_tracks(query, limit=getattr(args, "limit", 10))
        results = payload.get("results") or []
        if not results:
            notice("No neighbors found. Run /embed to give every track an embedding.")
            return 1
        seed = payload.get("seed") or {}
        section("Similar", seed.get("label") or query)
        table(
            [
                ColumnSpec("#", justify="right", style="dim"),
                "Song",
                "Artist",
                ColumnSpec("Sim", metric=True),
                ColumnSpec("Basis", style="dim"),
            ],
            [
                [
                    idx,
                    # Visible text unchanged; a known Spotify identity adds an
                    # OSC 8 hyperlink (terminals without support show the name).
                    link_text(r["song"], r["spotify_url"]),
                    r["artist"],
                    f"{r['similarity']:.2f}",
                    r["basis"],
                ]
                for idx, r in enumerate(results, 1)
            ],
        )
        caption("basis: context = /enrich'd semantic embedding · title = lexical 'name by artist'")
        to_playlist = getattr(args, "to_playlist", None)
        if to_playlist:
            chosen = [r["track_id"] for r in results]
            ok = cli.add_search_to_playlist(to_playlist, chosen)
            payload["wrote"] = {"playlist": to_playlist, "requested": len(chosen), "ok": bool(ok)}
        return 0


def _handle_debug(cli: "PlaylistCLI", args: Any) -> int:
    topic = getattr(args, "topic", "last")
    fmt = getattr(args, "format", "json")
    if topic == "track":
        if not getattr(args, "value", None):
            warning("Track ID required for debug track.")
            return 1
        payload = cli.debug_track(args.value)
    else:
        payload = cli.debug_last_search()
    if not payload:
        warning("No debug data available.")
        return 1
    if fmt == "table":
        _present_debug(payload, topic)
        return 0
    json_output(payload)
    return 0


def _handle_ingest(cli: "PlaylistCLI", args: Any) -> int:
    cli.ingest_tracks(args.source, args.name, args.time_range)
    return 0


def _handle_listen_sync(cli: "PlaylistCLI", args: Any) -> int:
    cli.sync_listen_history(args.limit)
    return 0


def _handle_import_history(cli: "PlaylistCLI", args: Any) -> int:
    """Import a GDPR extended-streaming-history export into the listen ledger.

    Streams the export (zip / extracted folder / single json) through
    ``gdpr_import.import_streaming_history``; event ids reuse the
    recently_played uuid5 recipe so re-imports and API-polled overlap enrich
    instead of duplicating. Progress is plain periodic text lines (works in
    both the console and the TUI RichLog — no tqdm, so TUNR_INTERACTIVE
    needs no special-casing).
    """
    json_mode = getattr(args, "json", False)
    dry_run = getattr(args, "dry_run", False)
    path = Path(args.path).expanduser()
    set_json_mode(json_mode)
    payload: Dict[str, Any] = {}
    try:
        section("Import History", f"{path}{' (dry run)' if dry_run else ''}")
        staged = "counted" if dry_run else "imported"
        progress = {"seen": 0, "imported": 0}

        def _progress(seen: int, imported: int) -> None:
            progress["seen"], progress["imported"] = seen, imported
            if seen and seen % 5000 == 0:
                info(f"… {seen:,} records scanned · {imported:,} plays {staged}")

        def _rollback() -> None:
            # The export parses lazily, so a failure mid-stream can leave an
            # uncommitted partial batch on the long-lived TUI connection; roll
            # it back so the next unrelated commit can't silently persist it.
            try:
                cli.repos.conn.rollback()
            except Exception:
                logger.debug("Rollback after failed import failed.", exc_info=True)

        try:
            payload = import_streaming_history(
                cli.repos,
                iter_streaming_records(path),
                dry_run=dry_run,
                on_progress=_progress,
            )
        except GdprImportError as exc:
            _rollback()
            payload = {
                "error": str(exc),
                "records_seen": progress["seen"],
                "imported_before_error": progress["imported"],
            }
            error(str(exc))
            if progress["seen"]:
                warning(
                    f"Import aborted after {progress['seen']:,} records "
                    f"({progress['imported']:,} plays staged). Full batches committed "
                    "before the failure were kept; the uncommitted tail was rolled "
                    "back. Re-running the import after fixing the export is safe "
                    "(idempotent)."
                )
            return 1
        except Exception as exc:
            _rollback()
            payload = {"error": str(exc)}
            raise

        verb = "Would import" if dry_run else "Imported"
        summary_panel(
            f"{verb} {payload['imported']:,} plays from {payload['files']} file(s) — "
            f"{payload['records_total']:,} records scanned; skipped "
            f"{payload['episodes_skipped']:,} podcast/episode rows and "
            f"{payload['missing_metadata']:,} rows without track metadata.",
            title="Import History",
        )
        caption("play counts include partial plays; aggregation applies the 30s rule via ms_played")
        caption("overlap with API-polled events dedupes only on exact timestamps")
        return 0
    finally:
        if json_mode:
            emit_json(payload)
        set_json_mode(False)


def _handle_pull(cli: "PlaylistCLI", args: Any) -> int:
    with json_payload(getattr(args, "json", False)) as out:
        out["payload"] = {}
        out["payload"] = cli.pull_spotify_library(
            liked_only=getattr(args, "liked_only", False),
            playlists_only=getattr(args, "playlists_only", False),
            full=getattr(args, "full", False),
        )
    return 1 if out["payload"].get("error") else 0


def _handle_rotate_played(cli: "PlaylistCLI", args: Any) -> int:
    # Deprecated alias for `rotate`; kept one release so existing muscle memory
    # and scripts get a redirect instead of an "unknown command" error.
    logger.warning("`rotate-played` is deprecated — use `rotate` instead.")
    cli.rotate_playlist_played(args.playlist, args.max_replace)
    return 0


def _handle_rotate(cli: "PlaylistCLI", args: Any) -> int:
    cli.rotate_playlist_played(args.playlist, args.max_replace, getattr(args, "dry_run", False))
    return 0


def _handle_backup(cli: "PlaylistCLI", args: Any) -> int:
    cli.backup_data(args.backup_name)
    return 0


def _handle_restore(cli: "PlaylistCLI", args: Any) -> int:
    cli.restore_data(args.backup_name)
    return 0


def _handle_restore_previous_rotation(cli: "PlaylistCLI", args: Any) -> int:
    cli.restore_previous_rotation(args.playlist, args.offset)
    return 0


def _handle_list_rotations(cli: "PlaylistCLI", args: Any) -> int:
    cli.list_rotations(args.playlist, args.generations)
    return 0


def _handle_list_backups(cli: "PlaylistCLI", args: Any) -> int:
    cli.list_backups()
    return 0


def _handle_auth_status(cli: "PlaylistCLI", args: Any) -> int:
    cli.auth_status()
    return 0


def _handle_auth_refresh(cli: "PlaylistCLI", args: Any) -> int:
    cli.auth_refresh()
    return 0


def _handle_auth_reset(cli: "PlaylistCLI", args: Any) -> int:
    cli.auth_reset(yes=getattr(args, "yes", False))
    return 0


def _handle_interactive(cli: "PlaylistCLI", args: Any) -> int:
    logger.info("Already running. Use the interactive UI directly.")
    return 0


# ui contract colors: green = succeeded, yellow = warning, red = failure only.
_DOCTOR_STATUS_STYLES = {"ok": "green", "warn": "yellow", "fail": "bold red"}


def _doctor_status_style(value: Any) -> Optional[str]:
    return _DOCTOR_STATUS_STYLES.get(str(value))


def _handle_doctor(cli: "PlaylistCLI", args: Any) -> int:
    """Offline integrity audit (/doctor): render doctor.run_checks as a status
    table (or the --json payload) and exit nonzero when any check failed."""
    json_mode = getattr(args, "json", False)
    set_json_mode(json_mode)
    payload: Dict[str, Any] = {}
    results: List[Any] = []
    try:
        conn = cli.repos.conn
        db_path = cli.storage.path
        expected_model = os.getenv("SEARCH_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
        results = run_doctor_checks(
            conn, db_path, doctor.default_backups_dir(), expected_model=expected_model
        )
        payload = doctor_results_payload(results)
        payload["db_path"] = str(db_path)
        section("Doctor", str(db_path))
        table(
            [
                ColumnSpec("Status", metric=_doctor_status_style),
                "Check",
                "Detail",
                "Remedy",
            ],
            [[r.status, r.name, r.detail, r.remedy or "—"] for r in results],
        )
        counts = payload["counts"]
        if counts["fail"]:
            # error() is never silenced by json mode (it would land on stderr);
            # in --json the payload itself carries the verdicts, so skip it.
            if not json_mode:
                error(f"{counts['fail']} check(s) failed — remedies above.", title="Doctor")
        elif counts["warn"]:
            warning(f"Healthy, with {counts['warn']} warning(s).")
        else:
            info("All checks passed.")
    finally:
        if json_mode:
            emit_json(payload)
        set_json_mode(False)
    return 1 if has_failures(results) else 0


def _handle_add(cli: "PlaylistCLI", args: Any) -> int:
    query = " ".join(getattr(args, "query", None) or [])
    ok = cli.add_track_to_playlist(
        query, args.to_playlist, track_id=getattr(args, "track_id", None)
    )
    return 0 if ok else 1


def _handle_remove(cli: "PlaylistCLI", args: Any) -> int:
    query = " ".join(getattr(args, "query", None) or [])
    ok = cli.remove_track_from_playlist(
        query, args.from_playlist, track_id=getattr(args, "track_id", None)
    )
    return 0 if ok else 1


def _handle_move(cli: "PlaylistCLI", args: Any) -> int:
    query = " ".join(getattr(args, "query", None) or [])
    ok = cli.move_track(
        query,
        args.from_playlist,
        args.to_playlist,
        track_id=getattr(args, "track_id", None),
    )
    return 0 if ok else 1


# Built after the handler functions so each name is already defined.
_COMMAND_HANDLERS: Dict[str, Callable[["PlaylistCLI", Any], int]] = {
    "import": _handle_import,
    "update": _handle_update,
    "stats": _handle_stats,
    "status": _handle_status,
    "profile": _handle_profile,
    "taste": _handle_taste,
    "view": _handle_view,
    "sync": _handle_sync,
    "extract": _handle_extract,
    "plan": _handle_plan,
    "diff": _handle_diff,
    "clean": _handle_clean,
    "search": _handle_search,
    "find": _handle_find,
    "undo": _handle_undo,
    "enrich": _handle_enrich,
    "sonic": _handle_sonic,
    "debug": _handle_debug,
    "ingest": _handle_ingest,
    "listen-sync": _handle_listen_sync,
    "import-history": _handle_import_history,
    "pull": _handle_pull,
    "rotate-played": _handle_rotate_played,
    "rotate": _handle_rotate,
    "backup": _handle_backup,
    "restore": _handle_restore,
    "restore-previous-rotation": _handle_restore_previous_rotation,
    "list-rotations": _handle_list_rotations,
    "list-backups": _handle_list_backups,
    "auth-status": _handle_auth_status,
    "auth-refresh": _handle_auth_refresh,
    "auth-reset": _handle_auth_reset,
    "interactive": _handle_interactive,
    "doctor": _handle_doctor,
    "embed": _handle_embed,
    "similar": _handle_similar,
    "add": _handle_add,
    "remove": _handle_remove,
    "move": _handle_move,
}


def dispatch_command(cli: "PlaylistCLI", command: str, args: object) -> int:
    """Execute a parsed command against the CLI via the command registry."""
    try:
        handler = _COMMAND_HANDLERS.get(command)
        if handler is None:
            logger.error(f"Unknown command: {command}")
            return 1
        return handler(cli, args)
    except Exception as e:
        # Backstop rollback: a handler that died mid-write (e.g. listen-sync's
        # upsert loop) must not leave an open transaction on the long-lived
        # TUI connection — the next unrelated command's commit would silently
        # persist the partial write. Only touch an ALREADY-OPEN connection;
        # never lazily create one just to roll it back.
        repos = getattr(cli, "_repos", None)
        if repos is not None:
            try:
                repos.conn.rollback()
            except Exception:
                logger.debug("Backstop rollback failed.", exc_info=True)
        # exc_info=True threads the traceback to every handler: the TUI's
        # UILogHandler formatter appends exc_text (so /debug errors and the
        # RichLog show the real traceback) and the CLI RichHandler renders a
        # rich traceback. The rc contract (return 1) is unchanged.
        logger.error("Command failed: %s", e, exc_info=True)
        return 1
