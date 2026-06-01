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
candidate back to the target by canonical id. Matching is exact (precision over
recall) — a track we can't confidently identify is left unenriched rather than
mislabelled with another song's context.
"""

from __future__ import annotations

from datetime import datetime

from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm

from .canonicalize import canonical_track_id, canonicalize_results
from .context import build_context_card
from .embeddings import EmbeddingModel
from .extract import extract_context

# Imported at module level so tests can monkeypatch `nextgen.enrich.run_providers`
# (the network/LLM seam) without spawning any subprocess.
from .providers import run_providers


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


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
    """Enrich one known track: fetch semantic context via deep search, store it,
    and re-embed from that context.

    Writes a ``track_context`` row and overwrites the track's ``track_embeddings``
    row (keyed by the DB ``track_id``) with a context-derived vector. The vector
    uses ``model_name`` so it stays consistent with the search pipeline — a
    mismatched model would make the pipeline wipe the embeddings table.

    Returns True if the deep-search results surfaced this track and it was
    enriched; False if no returned candidate matched the target track.
    """
    run = run_providers(query=f"{name} by {artist}")
    items = canonicalize_results(run.results)
    target = canonical_track_id(artist, name)
    match = next((item for item in items if item.get("track_id") == target), None)
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
    vector = EmbeddingModel(model_name).embed([card.context_text])[0]
    repos.embeddings.upsert(
        {
            "track_id": track_id,
            "model_name": model_name,
            "embedding_blob": encode_vector(vector),
            "embedding_dim": len(vector),
            "embedding_norm": vector_norm(vector),
            "strict_ratio": card.strict_ratio,
            "created_at": now,
        }
    )
    repos.conn.commit()
    return True
