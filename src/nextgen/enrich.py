"""Semantic enrichment for known library tracks.

Today's library embeddings are *lexical* — generated from ``"{name} by {artist}"``
during the legacy migration. This module backfills richer *semantic* context
(genre / mood / era / style, gathered by the deep-search providers) into
``track_context`` and RE-EMBEDS the track from that context, so downstream
ranking (`/taste`, `/find`) reflects what a track is *about* rather than just its
title. This is semantic, NOT acoustic — there is no audio-feature data source
available (Spotify audio-features is dead for this app).

The one piece the search pipeline never needed is a *known-track → context*
bridge: the providers are freeform-query shaped and return a list of discoveries,
so enriching one known track means synthesising a query, then matching a returned
candidate back to the target. Matching is high-confidence and tiered (exact id →
normalized artist+title → fuzzy gated at MATCH_THRESHOLD), absorbing cross-source
spelling variance (feat./&/diacritics/remaster suffixes) while still leaving a
track we can't confidently identify unenriched rather than mislabelled.
"""

from __future__ import annotations

import difflib
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm

from .canonicalize import canonical_track_id, canonicalize_results
from .context import build_context_card
from .embeddings import EmbeddingModel
from .extract import extract_context

# Imported at module level so tests can monkeypatch `nextgen.enrich.run_providers`
# (the network/LLM seam) without spawning any subprocess.
from .providers import ProviderRun, run_providers


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


# A returned candidate is accepted only at HIGH confidence. Cross-source spelling
# variance (feat./&/diacritics/punctuation/remaster+version suffixes) is normalized
# away; a residual near-miss is accepted only if BOTH artist AND title clear this bar.
MATCH_THRESHOLD = 0.9

_FEAT_RE = re.compile(r"\b(feat|ft|featuring)\b.*", re.IGNORECASE)
_BRACKET_RE = re.compile(r"[(\[][^)\]]*[)\]]")
_SUFFIX_RE = re.compile(
    r"\s*-\s*.*\b(remaster|remastered|radio edit|mono|stereo|version|mix|edit|live|acoustic)\b.*",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    """Normalize a song/artist string so cross-source spellings compare equal."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))  # strip diacritics
    text = text.lower()
    text = _BRACKET_RE.sub(" ", text)  # drop "(feat. X)", "[Remaster]"
    text = _SUFFIX_RE.sub(" ", text)  # drop "- 2011 Remaster", "- Radio Edit"
    text = _FEAT_RE.sub(" ", text)  # drop trailing "feat. X"
    text = text.replace("&", " and ")
    text = text.replace("'", "").replace("’", "")  # drop apostrophes (don't split contractions)
    text = re.sub(r"[^\w\s]", " ", text)  # remaining punctuation -> separators
    return re.sub(r"\s+", " ", text).strip()


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _match_target(items: List[Dict[str, Any]], artist: str, name: str) -> Optional[Dict[str, Any]]:
    """Pick the candidate that IS the target track, at high confidence.

    Tier 1: exact canonical id. Tier 2: normalized artist+title equality (absorbs
    feat./&/diacritics/punctuation/remaster noise). Tier 3: fuzzy, accepted only if
    BOTH normalized artist and title are >= MATCH_THRESHOLD similar. Returns None
    rather than a low-confidence guess — precision over recall, so a track we can't
    confidently identify is left unenriched, never mislabelled.
    """
    target = canonical_track_id(artist, name)
    for item in items:
        if item.get("track_id") == target:
            return item

    na, nt = _normalize(artist), _normalize(name)
    if na and nt:
        for item in items:
            if (
                _normalize(item.get("artist") or "") == na
                and _normalize(item.get("song") or "") == nt
            ):
                return item

    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for item in items:
        score = min(
            _ratio(_normalize(item.get("artist") or ""), na),
            _ratio(_normalize(item.get("song") or ""), nt),
        )
        if score > best_score:
            best_score, best = score, item
    return best if best_score >= MATCH_THRESHOLD else None


def _fetch_run(name: str, artist: str) -> ProviderRun:
    """The slow part: one deep-search fetch for a known track.

    Network/subprocess-only — touches no DB, no embedding model, no shared Python
    state — so it is safe to call concurrently from a thread pool.
    """
    return run_providers(query=f"{name} by {artist}")


def _apply_run(
    repos: Repositories,
    *,
    track_id: str,
    name: str,
    artist: str,
    run: ProviderRun,
    model: EmbeddingModel,
    strict_threshold: float,
    lenient_threshold: float,
) -> bool:
    """The fast part: match a fetched run to the target, store context, re-embed.

    Touches the SQLite connection (single-threaded-only) and the embedding model,
    so callers MUST invoke this serially — never from worker threads. Writes a
    ``track_context`` row and overwrites the track's ``track_embeddings`` row
    (keyed by the DB ``track_id``) with a context-derived vector under the model's
    name (kept consistent with the pipeline, or it would wipe the table).

    The fallible work (embedding) runs BEFORE any DB write, so a failure stages
    nothing — paired with the caller's rollback-on-error, a "failed" track can
    never leave an orphaned context row to be flushed by a later track's commit.
    Returns True if a candidate matched the target and was written.
    """
    items = canonicalize_results(run.results)
    match = _match_target(items, artist, name)
    if match is None:
        return False

    extracted = extract_context(
        item=match,
        strict_threshold=strict_threshold,
        lenient_threshold=lenient_threshold,
    )
    card = build_context_card(
        song=name,
        artist=artist,
        year=match.get("year"),
        extracted=extracted,
        strict_threshold=strict_threshold,
    )
    # Embed first: this is the fallible step, and it must not run after a staged
    # context.upsert (an implicit open transaction would otherwise leak it).
    vector = model.embed([card.context_text])[0]
    now = _now()
    repos.context.upsert(
        {
            "track_id": track_id,
            "context_text": card.context_text,
            "strict_text": card.strict_text,
            "lenient_text": card.lenient_text,
            "fields_json": card.fields_json,
            "sources_json": card.sources_json,
            "strict_ratio": card.strict_ratio,
            "context_version": "v1",
            "generated_at": now,
        }
    )
    repos.embeddings.upsert(
        {
            "track_id": track_id,
            "model_name": model.model_name,
            "embedding_blob": encode_vector(vector),
            "embedding_dim": len(vector),
            "embedding_norm": vector_norm(vector),
            "strict_ratio": card.strict_ratio,
            "created_at": now,
        }
    )
    repos.conn.commit()
    return True


def enrich_track(
    repos: Repositories,
    *,
    track_id: str,
    name: str,
    artist: str,
    model_name: str,
    strict_threshold: float,
    lenient_threshold: float,
) -> bool:
    """Enrich one known track end-to-end (fetch + apply). For batches, prefer
    ``enrich_tracks`` which fans out the fetch and applies serially."""
    return _apply_run(
        repos,
        track_id=track_id,
        name=name,
        artist=artist,
        run=_fetch_run(name, artist),
        model=EmbeddingModel(model_name),
        strict_threshold=strict_threshold,
        lenient_threshold=lenient_threshold,
    )


def enrich_tracks(
    repos: Repositories,
    tracks: List[Tuple[str, str, str]],
    *,
    model_name: str,
    strict_threshold: float,
    lenient_threshold: float,
    concurrency: int = 8,
    on_result: Optional[Callable[[str, str, str], None]] = None,
) -> Dict[str, int]:
    """Enrich many tracks, parallelizing the slow deep-search fetch.

    `tracks` is a list of (track_id, name, artist). The network fetch fans out
    across `concurrency` worker threads; the DB writes + re-embed (`_apply_run`)
    run SERIALLY on this thread as each fetch completes — so the single-threaded
    SQLite connection is never touched concurrently. `on_result(status, name,
    artist)` (status in {"enriched","skipped","failed"}) is invoked on this thread
    for progress. Returns counts {"enriched","skipped","failed"}.
    """
    counts = {"enriched": 0, "skipped": 0, "failed": 0}
    if not tracks:
        return counts

    def _report(status: str, name: str, artist: str) -> None:
        counts[status] += 1
        if on_result is not None:
            on_result(status, name, artist)

    # Build the embedding model ONCE (loaded from disk per construction); it is used
    # only on this thread inside the serial apply loop.
    model = EmbeddingModel(model_name)
    workers = max(1, min(concurrency, len(tracks)))

    # Pin the providers' INTERNAL per-query fan-out to 1 for the whole batch, so
    # total in-flight calls ~= our outer `concurrency`, not concurrency x N x
    # providers (which would self-throttle into 429s). Set before any worker spawns
    # (subprocesses copy os.environ at spawn) and restore afterward.
    prev_parallel = os.environ.get("WEB_SEARCH_PARALLEL_PER_PROVIDER")
    os.environ["WEB_SEARCH_PARALLEL_PER_PROVIDER"] = "1"
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_track = {
                executor.submit(_fetch_run, name, artist): (track_id, name, artist)
                for track_id, name, artist in tracks
            }
            for future in as_completed(future_to_track):
                track_id, name, artist = future_to_track[future]
                try:
                    run = future.result()
                except Exception:
                    _report("failed", name, artist)
                    continue
                try:
                    matched = _apply_run(
                        repos,
                        track_id=track_id,
                        name=name,
                        artist=artist,
                        run=run,
                        model=model,
                        strict_threshold=strict_threshold,
                        lenient_threshold=lenient_threshold,
                    )
                except Exception:
                    # Discard any partially-staged write so it can't be flushed by
                    # the next track's commit (orphaned context row otherwise).
                    repos.conn.rollback()
                    _report("failed", name, artist)
                    continue
                _report("enriched" if matched else "skipped", name, artist)
    finally:
        if prev_parallel is None:
            os.environ.pop("WEB_SEARCH_PARALLEL_PER_PROVIDER", None)
        else:
            os.environ["WEB_SEARCH_PARALLEL_PER_PROVIDER"] = prev_parallel
    return counts
