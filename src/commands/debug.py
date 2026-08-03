"""/debug presenters — render debug payloads as tables (headless + TUI).

Moved verbatim from main.py (PR 4 of the decomposition); visible output
unchanged. The TUI still carries its own drifted debug-rendering twin in
interactive_app — unifying the two through dispatch_command is Phase C.
"""

from __future__ import annotations

from ui import key_value_table, link_text, section, spotify_track_url, subsection, table


def _present_debug_track(payload: dict) -> None:
    """Render the `debug track` payload as tables (output unchanged)."""
    track = payload.get("track") or {}
    context = payload.get("context") or {}
    sources = payload.get("sources") or []
    embedding = payload.get("embedding") or {}
    listens = payload.get("listens") or []
    section("Debug", "Track")
    rows = [
        ["Track ID", track.get("track_id") or ""],
        ["Name", track.get("name") or ""],
        ["Artist ID", track.get("artist_id") or ""],
        ["Spotify ID", track.get("spotify_id") or ""],
        ["Spotify URL", track.get("spotify_url") or ""],
        ["Release", track.get("release_date") or ""],
        ["Status", track.get("status") or ""],
    ]
    if payload.get("resolved_rank"):
        rows.append(["Resolved Rank", payload.get("resolved_rank")])
    key_value_table(rows)
    if context:
        subsection("Context")
        key_value_table(
            [
                ["Strict Ratio", context.get("strict_ratio")],
                ["Context Text", (context.get("context_text") or "")[:200]],
            ]
        )
    if sources:
        subsection("Sources")
        table(
            ["#", "URL", "Title", "Snippet", "Provider", "Strict"],
            [
                [
                    idx,
                    s.get("url") or "",
                    s.get("title") or "",
                    s.get("snippet") or "",
                    s.get("provider") or "",
                    "yes" if s.get("is_strict") else "no",
                ]
                for idx, s in enumerate(sources, 1)
            ],
        )
    if embedding:
        subsection("Embedding")
        key_value_table(
            [
                ["Model", embedding.get("model_name") or ""],
                ["Dimensions", embedding.get("embedding_dim") or ""],
                ["Norm", embedding.get("embedding_norm")],
            ]
        )
    if listens:
        subsection("Listen Events")
        table(
            ["#", "Played At", "Source"],
            [
                [idx, event.get("played_at") or "", event.get("source") or ""]
                for idx, event in enumerate(listens[:10], 1)
            ],
        )


def _present_debug_last_search(payload: dict) -> None:
    """Render the `debug last-search` payload as tables (output unchanged)."""
    run = payload.get("run") or {}
    candidates = payload.get("candidates") or []
    summary = payload.get("summary") or {}
    section("Debug", "Last Search")
    key_value_table(
        [
            ["Run ID", run.get("run_id")],
            ["Started", run.get("started_at")],
            ["Finished", run.get("finished_at")],
            ["Status", run.get("status")],
            ["Results", len(candidates)],
            ["Cached", summary.get("cached")],
            ["Avg strict ratio", f"{summary.get('avg_strict_ratio', 0):.2f}"],
            ["Missing context", summary.get("missing_context", 0)],
            ["Model", summary.get("model_name") or ""],
        ]
    )
    score_config = summary.get("score_config") or {}
    if score_config:
        subsection("Score Config")
        key_value_table(
            [
                ["Base", score_config.get("base_weight")],
                ["Strict", score_config.get("strict_weight")],
                ["Source", score_config.get("source_weight")],
                ["Year", score_config.get("year_weight")],
                ["Year tol", score_config.get("year_tolerance")],
                ["Source cap", score_config.get("source_cap")],
                ["Year target", score_config.get("year_target")],
            ]
        )
    if candidates:
        preview_rows = []
        for idx, candidate in enumerate(candidates[:10], 1):
            track = candidate.get("track") or {}
            artist_label = track.get("artist_name") or track.get("artist_id") or ""
            label = f"{track.get('name', '')} — {artist_label}".strip(" —")
            preview_rows.append(
                [
                    idx,
                    # Visible label unchanged; a known Spotify id adds a hyperlink.
                    link_text(label, spotify_track_url(track.get("spotify_id"))),
                    candidate.get("track_id") or "",
                ]
            )
        subsection("Top Results (IDs)")
        table(["#", "Track", "Track ID"], preview_rows)


def _present_debug(payload: dict, topic: str) -> None:
    """Render a debug payload as tables, dispatching on topic."""
    if topic == "track":
        _present_debug_track(payload)
    else:
        _present_debug_last_search(payload)
