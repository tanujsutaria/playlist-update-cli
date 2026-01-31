from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from storage.cache import compute_query_hash
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm

from .canonicalize import canonicalize_results
from .context import build_context_card
from .extract import extract_context
from .embeddings import EmbeddingModel
from .providers import run_providers
from .scoring import ScoreConfig, rank_scores, score_candidates


logger = logging.getLogger(__name__)


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_DECADE_RE = re.compile(r"\b((?:19|20)?\d{2})s\b")


def _extract_year_target(query: str) -> Optional[int]:
    match = _YEAR_RE.search(query)
    if match:
        return int(match.group(0))
    match = _DECADE_RE.search(query.lower())
    if not match:
        return None
    token = match.group(1)
    if len(token) == 2:
        decade = int(token)
        if decade >= 50:
            base = 1900 + decade
        else:
            base = 2000 + decade
    else:
        base = int(token)
        if base < 1900:
            base += 1900
    return base + 5


ProgressCallback = Callable[[str], None]


@dataclass
class SearchResult:
    track_id: str
    song: str
    artist: str
    year: Optional[str]
    score: float
    strict_ratio: float
    sources: List[str]
    providers: List[str]


ResultCallback = Callable[[SearchResult, int, int], None]


class SearchPipeline:
    def __init__(
        self,
        repos: Repositories,
        model_name: str = "all-mpnet-base-v2",
        strict_threshold: float = 0.6,
        lenient_threshold: float = 0.75,
        score_config: Optional[ScoreConfig] = None,
    ) -> None:
        self.repos = repos
        self.model_name = model_name
        self.strict_threshold = strict_threshold
        self.lenient_threshold = lenient_threshold
        self.score_config = score_config or ScoreConfig()
        self.last_cached = False

    def _now(self) -> str:
        return datetime.utcnow().isoformat() + "Z"

    def _latest_run_id(self, query_hash: str) -> Optional[str]:
        row = self.repos.conn.execute(
            """
            SELECT run_id FROM search_runs
            WHERE query_hash = ? AND status = 'ok'
            ORDER BY started_at DESC
            LIMIT 1;
            """,
            (query_hash,),
        ).fetchone()
        if not row:
            return None
        return row["run_id"] if "run_id" in row.keys() else row[0]

    def _load_cached_results(self, run_id: str) -> List[SearchResult]:
        rows = self.repos.conn.execute(
            """
            SELECT sc.track_id, sc.score_final, sc.strict_ratio,
                   t.name, t.artist_id, t.release_date,
                   a.name AS artist_name,
                   tc.sources_json
            FROM search_candidates sc
            JOIN tracks t ON t.track_id = sc.track_id
            LEFT JOIN artists a ON a.artist_id = t.artist_id
            LEFT JOIN track_context tc ON tc.track_id = sc.track_id
            WHERE sc.run_id = ?
            ORDER BY sc.rank ASC;
            """,
            (run_id,),
        ).fetchall()

        results: List[SearchResult] = []
        for row in rows:
            sources_json = row["sources_json"] if "sources_json" in row.keys() else None
            sources = json.loads(sources_json) if sources_json else []
            results.append(
                SearchResult(
                    track_id=row["track_id"],
                    song=row["name"],
                    artist=row["artist_name"] or row["artist_id"],
                    year=row["release_date"],
                    score=row["score_final"] or 0.0,
                    strict_ratio=row["strict_ratio"] or 0.0,
                    sources=sources,
                    providers=[],
                )
            )
        return results

    def run(
        self,
        query: str,
        expanded: bool = False,
        progress: Optional[ProgressCallback] = None,
        on_result: Optional[ResultCallback] = None,
    ) -> Tuple[List[SearchResult], str]:
        if progress:
            progress("cache")

        query_hash = compute_query_hash(query, {"expanded": expanded, "model": self.model_name})
        cached_run_id = self._latest_run_id(query_hash)
        if cached_run_id:
            cached_results = self._load_cached_results(cached_run_id)
            if cached_results:
                self.last_cached = True
                return cached_results, cached_run_id
        self.last_cached = False

        if progress:
            progress("search")

        # Placeholder for eventual provider-specific progress updates.
        provider_run = run_providers(query=query, expanded=expanded)

        ensure_schema(self.repos.conn)
        run_id = str(uuid.uuid4())
        now = self._now()

        constraints_payload = dict(provider_run.constraints or {})
        constraints_payload["expanded"] = expanded
        self.repos.queries.upsert(
            {
                "query_hash": query_hash,
                "query_text": query,
                "constraints_json": json.dumps(constraints_payload),
                "embedding_blob": None,
                "embedding_dim": None,
                "model_name": self.model_name,
                "created_at": now,
                "last_used_at": now,
            }
        )

        self.repos.runs.insert(
            {
                "run_id": run_id,
                "query_hash": query_hash,
                "provider": "combined",
                "expanded": 1 if expanded else 0,
                "status": "ok",
                "error": None,
                "started_at": now,
                "finished_at": now,
                "results_count": len(provider_run.results),
            }
        )

        track_ids: List[str] = []
        context_texts: List[str] = []
        strict_ratios: List[float] = []
        metadata_items: List[Dict[str, object]] = []
        processed_items: List[Dict[str, object]] = []

        if progress:
            progress("extract")

        canonical_results = canonicalize_results(provider_run.results)
        for idx, item in enumerate(canonical_results, 1):
            song = (item.get("song") or "").strip()
            artist = (item.get("artist") or "").strip()
            if not song or not artist:
                continue
            artist_key = artist.lower()
            track_id = item.get("track_id") or f"{artist_key}|||{song.lower()}"
            track_ids.append(track_id)

            year = item.get("year")
            extracted = extract_context(
                item=item,
                strict_threshold=self.strict_threshold,
                lenient_threshold=self.lenient_threshold,
            )
            if item.get("_context_missing"):
                logger.warning("Missing context fields for %s by %s", song, artist)
            context_card = build_context_card(
                song=song,
                artist=artist,
                year=year,
                extracted=extracted,
                strict_threshold=self.strict_threshold,
            )

            self.repos.tracks.upsert(
                {
                    "track_id": track_id,
                    "spotify_id": item.get("spotify_uri"),
                    "name": song,
                    "artist_id": artist_key,
                    "album_name": None,
                    "release_date": year,
                    "duration_ms": None,
                    "explicit": None,
                    "popularity": None,
                    "spotify_url": item.get("spotify_url"),
                    "status": "candidate",
                    "last_decision": None,
                    "decision_reason": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            self.repos.artists.upsert(
                artist_id=artist_key,
                name=artist,
                genres_json=json.dumps([]),
                popularity=None,
                updated_at=now,
            )

            self.repos.context.upsert(
                {
                    "track_id": track_id,
                    "context_text": context_card.context_text,
                    "strict_text": context_card.strict_text,
                    "lenient_text": context_card.lenient_text,
                    "fields_json": context_card.fields_json,
                    "sources_json": context_card.sources_json,
                    "strict_ratio": context_card.strict_ratio,
                    "context_version": "v1",
                    "generated_at": now,
                }
            )

            provider_label = ",".join(item.get("providers") or provider_run.providers)
            detail_map: Dict[str, Dict[str, object]] = {}
            for detail in item.get("source_details") or []:
                if not isinstance(detail, dict):
                    continue
                url = detail.get("url")
                if url:
                    detail_map[str(url)] = detail

            for source in extracted.sources:
                source_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{track_id}|{source}").hex
                detail = detail_map.get(source, {})
                self.repos.sources.upsert(
                    {
                        "source_id": source_id,
                        "track_id": track_id,
                        "url": source,
                        "title": detail.get("title"),
                        "snippet": detail.get("snippet"),
                        "provider": provider_label,
                        "is_strict": 1,
                        "retrieved_at": now,
                    }
                )

            for url, detail in detail_map.items():
                if url in extracted.sources:
                    continue
                source_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{track_id}|{url}").hex
                self.repos.sources.upsert(
                    {
                        "source_id": source_id,
                        "track_id": track_id,
                        "url": url,
                        "title": detail.get("title"),
                        "snippet": detail.get("snippet"),
                        "provider": provider_label,
                        "is_strict": 0,
                        "retrieved_at": now,
                    }
                )

            context_texts.append(context_card.context_text)
            strict_ratios.append(context_card.strict_ratio)
            metadata_items.append(
                {
                    "year": year,
                    "sources_count": len(item.get("sources") or []),
                }
            )
            processed_items.append(item)

            if progress and idx % 25 == 0:
                progress(f"extract {idx}/{len(canonical_results)}")

        if not track_ids:
            return [], run_id

        if progress:
            progress("embed")

        model = EmbeddingModel(self.model_name)
        track_vectors = model.embed(context_texts)
        query_vector = model.embed([query])[0] if query else []

        for track_id, vec, strict_ratio in zip(track_ids, track_vectors, strict_ratios):
            self.repos.embeddings.upsert(
                {
                    "track_id": track_id,
                    "model_name": self.model_name,
                    "embedding_blob": encode_vector(vec),
                    "embedding_dim": len(vec),
                    "embedding_norm": vector_norm(vec),
                    "strict_ratio": strict_ratio,
                    "created_at": now,
                }
            )

        self.repos.queries.upsert(
            {
                "query_hash": query_hash,
                "query_text": query,
                "constraints_json": json.dumps(constraints_payload),
                "embedding_blob": encode_vector(query_vector),
                "embedding_dim": len(query_vector),
                "model_name": self.model_name,
                "created_at": now,
                "last_used_at": now,
            }
        )

        if progress:
            progress("score")

        base_config = self.score_config or ScoreConfig()
        score_config = ScoreConfig(
            strict_weight=base_config.strict_weight,
            base_weight=base_config.base_weight,
            source_weight=base_config.source_weight,
            year_weight=base_config.year_weight,
            year_tolerance=base_config.year_tolerance,
            source_cap=base_config.source_cap,
            year_target=_extract_year_target(query),
        )
        scores = score_candidates(
            query_vector,
            track_vectors,
            strict_ratios,
            score_config,
            metadata=metadata_items,
        )
        order = rank_scores(scores)

        results_out: List[SearchResult] = []
        total = len(order)
        for rank, idx in enumerate(order, 1):
            track_id = track_ids[idx]
            score = scores[idx]
            item = processed_items[idx]
            song = item.get("song") or item.get("name") or ""
            artist = item.get("artist") or ""
            year = item.get("year")
            sources = item.get("sources") or []
            providers = item.get("providers") or provider_run.providers or []

            self.repos.candidates.upsert(
                {
                    "run_id": run_id,
                    "track_id": track_id,
                    "rank": rank,
                    "score_text": score,
                    "score_audio": None,
                    "score_final": score,
                    "strict_ratio": strict_ratios[idx],
                    "lenient_ratio": 1.0 - strict_ratios[idx],
                    "sources_count": len(sources),
                }
            )

            results_out.append(
                SearchResult(
                    track_id=track_id,
                    song=song,
                    artist=artist,
                    year=year,
                    score=score,
                    strict_ratio=strict_ratios[idx],
                    sources=sources,
                    providers=providers,
                )
            )

            if on_result:
                on_result(results_out[-1], rank, total)

            if progress and rank % 25 == 0:
                progress(f"score {rank}/{len(order)}")

        if progress:
            progress("cache")

        return results_out, run_id
