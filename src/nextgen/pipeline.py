from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from storage.cache import compute_query_hash
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import decode_vector, encode_vector, vector_norm
from web_search import extract_constraints, extract_requested_metrics

from .canonicalize import canonicalize_results
from .context import build_context_card
from .embeddings import EmbeddingModel
from .extract import extract_context
from .providers import run_providers
from .scoring import SearchScoreConfig, rank_scores, score_candidates

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
        # Two-digit decades (e.g. "30s") are ambiguous: prefer the 20xx reading
        # only when it is not in the future, otherwise fall back to 19xx. This
        # keeps "30s"/"40s" mapping to 1935/1945 rather than future years, while
        # "20s" still resolves to the current-century decade once it is reached.
        decade = int(token)
        candidate = 2000 + decade
        base = candidate if candidate <= datetime.now().year else 1900 + decade
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
    # Per-track metrics surfaced by the provider (e.g. monthly_listeners,
    # similarity). Carried so the display-path constraint filter can act on them.
    # Defaulted + last so positional/kwargs constructors that omit it stay valid.
    metrics: Dict[str, Any] = field(default_factory=dict)


ResultCallback = Callable[[SearchResult, int, int], None]


class SearchPipeline:
    def __init__(
        self,
        repos: Repositories,
        model_name: str = "all-mpnet-base-v2",
        strict_threshold: float = 0.6,
        lenient_threshold: float = 0.75,
        score_config: Optional[SearchScoreConfig] = None,
    ) -> None:
        self.repos = repos
        self.model_name = model_name
        self.strict_threshold = strict_threshold
        self.lenient_threshold = lenient_threshold
        self.score_config = score_config or SearchScoreConfig()
        self.last_cached = False
        self.last_score_config: Optional[SearchScoreConfig] = None
        # Run-level scalars from the provider, surfaced on the instance (mirroring
        # `last_cached`) rather than widening run()'s 2-tuple return — so existing
        # callers/tests that unpack `(results, run_id)` stay untouched. search_songs
        # reads these via getattr() with defaults.
        self.last_summary: Optional[str] = None
        self.last_constraints: Dict[str, Any] = {}
        self.last_requested_metrics: List[str] = []

    def _now(self) -> str:
        return datetime.utcnow().isoformat() + "Z"

    def _requested_metrics_for_query(self, query: str) -> List[str]:
        """Reconstruct run_deep_search's requested_metrics from the query text.

        Used only on cache hits (where the provider isn't re-invoked). Mirrors the
        derivation in web_search.run_deep_search so fresh and cached runs agree.
        """
        metrics = list(extract_requested_metrics(query))
        constraints = extract_constraints(query)
        if (
            constraints.get("max_monthly_listeners") or constraints.get("min_monthly_listeners")
        ) and "monthly_listeners" not in metrics:
            metrics.append("monthly_listeners")
        if constraints.get("similarity_requested") and "similarity" not in metrics:
            metrics.append("similarity")
        return metrics

    def _score_config_payload(self, score_config: SearchScoreConfig) -> Dict[str, object]:
        return {
            "strict_threshold": self.strict_threshold,
            "lenient_threshold": self.lenient_threshold,
            "base_weight": score_config.base_weight,
            "strict_weight": score_config.strict_weight,
            "source_weight": score_config.source_weight,
            "year_weight": score_config.year_weight,
            "year_tolerance": score_config.year_tolerance,
            "source_cap": score_config.source_cap,
            "year_target": score_config.year_target,
        }

    def _score_config_hash(self, score_config: SearchScoreConfig) -> str:
        payload = self._score_config_payload(score_config)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _build_score_config(self, query: str) -> SearchScoreConfig:
        base_config = self.score_config or SearchScoreConfig()
        score_config = SearchScoreConfig(
            strict_weight=base_config.strict_weight,
            base_weight=base_config.base_weight,
            source_weight=base_config.source_weight,
            year_weight=base_config.year_weight,
            year_tolerance=base_config.year_tolerance,
            source_cap=base_config.source_cap,
            year_target=_extract_year_target(query),
        )
        self.last_score_config = score_config
        return score_config

    def _ensure_model_consistency(self) -> None:
        models: set[str] = set()
        for row in self.repos.conn.execute(
            "SELECT DISTINCT model_name FROM track_embeddings WHERE model_name IS NOT NULL;"
        ).fetchall():
            models.add(row["model_name"] if isinstance(row, dict) else row[0])
        for row in self.repos.conn.execute(
            "SELECT DISTINCT model_name FROM queries WHERE model_name IS NOT NULL;"
        ).fetchall():
            models.add(row["model_name"] if isinstance(row, dict) else row[0])

        models.discard(None)
        if not models:
            return
        if models != {self.model_name}:
            logger.warning(
                "Embedding model changed (%s -> %s); clearing cached embeddings and runs.",
                models,
                self.model_name,
            )
            self.repos.conn.execute("DELETE FROM track_embeddings;")
            self.repos.conn.execute("DELETE FROM queries;")
            self.repos.conn.execute("DELETE FROM search_candidates;")
            self.repos.conn.execute("DELETE FROM search_runs;")
            self.repos.conn.commit()

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
                   sc.metrics_json,
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
            metrics_json = row["metrics_json"] if "metrics_json" in row.keys() else None
            # Guard against corrupt/garbage JSON in the DB so a single bad row can't
            # crash the whole cached search; degrade to empty (lenient).
            try:
                sources = json.loads(sources_json) if sources_json else []
            except (TypeError, ValueError):
                sources = []
            try:
                metrics = json.loads(metrics_json) if metrics_json else {}
            except (TypeError, ValueError):
                metrics = {}
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
                    metrics=metrics,
                )
            )
        return results

    def _rescore_cached_run(
        self,
        run_id: str,
        query_text: str,
        query_hash: str,
        score_config: SearchScoreConfig,
        score_config_hash: str,
    ) -> None:
        rows = self.repos.conn.execute(
            """
            SELECT sc.track_id, sc.strict_ratio, sc.sources_count,
                   t.release_date, tc.context_text
            FROM search_candidates sc
            JOIN tracks t ON t.track_id = sc.track_id
            LEFT JOIN track_context tc ON tc.track_id = sc.track_id
            WHERE sc.run_id = ?
            ORDER BY sc.rank ASC;
            """,
            (run_id,),
        ).fetchall()
        if not rows:
            return

        track_ids = [row["track_id"] if isinstance(row, dict) else row[0] for row in rows]
        embedding_rows = {}
        if track_ids:
            placeholders = ",".join(["?"] * len(track_ids))
            for row in self.repos.conn.execute(
                f"SELECT track_id, embedding_blob, embedding_dim FROM track_embeddings WHERE track_id IN ({placeholders});",
                track_ids,
            ).fetchall():
                track_id = row["track_id"] if isinstance(row, dict) else row[0]
                embedding_rows[track_id] = row

        missing_contexts: List[str] = []
        missing_ids: List[str] = []
        for row in rows:
            track_id = row["track_id"] if isinstance(row, dict) else row[0]
            if track_id not in embedding_rows:
                context_text = row["context_text"] if isinstance(row, dict) else row[4]
                if context_text:
                    missing_ids.append(track_id)
                    missing_contexts.append(context_text)

        if missing_ids:
            model = EmbeddingModel(self.model_name)
            vectors = model.embed(missing_contexts)
            now = self._now()
            for track_id, vec in zip(missing_ids, vectors):
                self.repos.embeddings.upsert(
                    {
                        "track_id": track_id,
                        "model_name": self.model_name,
                        "embedding_blob": encode_vector(vec),
                        "embedding_dim": len(vec),
                        "embedding_norm": vector_norm(vec),
                        "strict_ratio": None,
                        "created_at": now,
                    }
                )
            self.repos.conn.commit()
            for row in self.repos.conn.execute(
                f"SELECT track_id, embedding_blob, embedding_dim FROM track_embeddings WHERE track_id IN ({placeholders});",
                track_ids,
            ).fetchall():
                track_id = row["track_id"] if isinstance(row, dict) else row[0]
                embedding_rows[track_id] = row

        query_row = self.repos.queries.get(query_hash) or {}
        query_blob = query_row.get("embedding_blob")
        query_vec = decode_vector(query_blob) if query_blob else []
        if not query_vec:
            model = EmbeddingModel(self.model_name)
            query_vec = model.embed([query_text])[0] if query_text else []
            self.repos.queries.upsert(
                {
                    "query_hash": query_hash,
                    "query_text": query_text,
                    "constraints_json": query_row.get("constraints_json"),
                    "embedding_blob": encode_vector(query_vec),
                    "embedding_dim": len(query_vec),
                    "model_name": self.model_name,
                    "created_at": query_row.get("created_at") or self._now(),
                    "last_used_at": self._now(),
                }
            )

        track_vectors: List[List[float]] = []
        strict_ratios: List[float] = []
        metadata_items: List[Dict[str, object]] = []
        for row in rows:
            track_id = row["track_id"] if isinstance(row, dict) else row[0]
            embedding_row = embedding_rows.get(track_id)
            vec = decode_vector(embedding_row["embedding_blob"]) if embedding_row else []
            track_vectors.append(vec)
            ratio = row["strict_ratio"] if isinstance(row, dict) else row[1]
            strict_ratios.append(float(ratio) if ratio is not None else 0.0)
            release_date = row["release_date"] if isinstance(row, dict) else row[3]
            sources_count = row["sources_count"] if isinstance(row, dict) else row[2]
            metadata_items.append(
                {
                    "year": release_date,
                    "sources_count": sources_count or 0,
                }
            )

        try:
            scores = score_candidates(
                query_vec,
                track_vectors,
                strict_ratios,
                score_config,
                metadata=metadata_items,
            )
            order = rank_scores(scores)

            for rank, idx in enumerate(order, 1):
                track_id = track_ids[idx]
                score = scores[idx]
                strict_ratio = strict_ratios[idx]
                sources_count = metadata_items[idx].get("sources_count") or 0
                self.repos.candidates.upsert(
                    {
                        "run_id": run_id,
                        "track_id": track_id,
                        "rank": rank,
                        "score_text": score,
                        "score_audio": None,
                        "score_final": score,
                        "strict_ratio": strict_ratio,
                        "lenient_ratio": 1.0 - float(strict_ratio),
                        "sources_count": sources_count,
                    }
                )

            self.repos.conn.execute(
                "UPDATE search_runs SET score_config_hash = ? WHERE run_id = ?;",
                (score_config_hash, run_id),
            )
            self.repos.conn.commit()
        except Exception as exc:
            logger.error("Failed to rescore cached run %s: %s", run_id, exc)
            self.repos.conn.rollback()

    def run(
        self,
        query: str,
        expanded: bool = False,
        progress: Optional[ProgressCallback] = None,
        on_result: Optional[ResultCallback] = None,
    ) -> Tuple[List[SearchResult], str]:
        ensure_schema(self.repos.conn)
        self._ensure_model_consistency()
        score_config = self._build_score_config(query)
        score_config_hash = self._score_config_hash(score_config)
        if progress:
            progress("cache")

        query_hash = compute_query_hash(query, {"expanded": expanded, "model": self.model_name})
        cached_run_id = self._latest_run_id(query_hash)
        if cached_run_id:
            run_row = self.repos.runs.get(cached_run_id) or {}
            cached_hash = run_row.get("score_config_hash")
            if cached_hash != score_config_hash:
                self._rescore_cached_run(
                    run_id=cached_run_id,
                    query_text=query,
                    query_hash=query_hash,
                    score_config=score_config,
                    score_config_hash=score_config_hash,
                )
            cached_results = self._load_cached_results(cached_run_id)
            if cached_results:
                self.last_cached = True
                # Rehydrate the run-level scalars so a cache hit is as informative
                # as a fresh run: summary from the persisted column, constraints
                # from the stored query row, requested_metrics recomputed from text.
                self.last_summary = (run_row.get("summary") if run_row else None) or None
                query_row = self.repos.queries.get(query_hash) or {}
                try:
                    loaded = json.loads(query_row.get("constraints_json") or "{}")
                except (TypeError, ValueError):
                    logger.warning(
                        "Cached run %s has unparseable constraints_json; "
                        "constraints not applied on this cache hit.",
                        cached_run_id,
                    )
                    loaded = {}
                loaded.pop("expanded", None)  # parity with the fresh path (which omits it)
                self.last_constraints = loaded
                self.last_requested_metrics = self._requested_metrics_for_query(query)
                return cached_results, cached_run_id
        self.last_cached = False

        if progress:
            progress("search")

        # Placeholder for eventual provider-specific progress updates.
        provider_run = run_providers(query=query, expanded=expanded)
        # Surface the run-level scalars now, before the `if not track_ids: return`
        # early-out at the end of extraction, so they're populated even when the
        # provider yields nothing usable.
        self.last_summary = provider_run.summary or None
        self.last_constraints = dict(provider_run.constraints or {})
        self.last_requested_metrics = list(provider_run.requested_metrics or [])

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
        self.repos.conn.commit()

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
                "score_config_hash": score_config_hash,
                "results_count": len(provider_run.results),
                "summary": provider_run.summary,
            }
        )
        self.repos.conn.commit()

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

            self.repos.artists.upsert(
                artist_id=artist_key,
                name=artist,
                genres_json=json.dumps([]),
                popularity=None,
                updated_at=now,
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

        self.repos.conn.commit()

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

            metrics = item.get("metrics") or {}
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
                    "metrics_json": json.dumps(metrics),
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
                    metrics=metrics,
                )
            )

            if on_result:
                on_result(results_out[-1], rank, total)

            if progress and rank % 25 == 0:
                progress(f"score {rank}/{len(order)}")

        if progress:
            progress("cache")

        self.repos.conn.commit()
        return results_out, run_id
