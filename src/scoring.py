from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from models import Song

logger = logging.getLogger(__name__)


@dataclass
class ScoreConfig:
    """Configuration for match scoring."""
    strategy: str = "local"  # local | web | hybrid
    query: Optional[str] = None
    seed_generations: int = 3
    seed_limit: int = 50
    web_max_candidates: int = 50
    web_timeout_sec: int = 60


@dataclass
class PlaylistProfile:
    playlist_name: str
    query: Optional[str]
    seed_songs: List[Song]
    seed_text: str
    embedding: Optional[np.ndarray] = None
    audio_vector: Optional[np.ndarray] = None


class ScoreProvider:
    """Interface for scoring providers."""

    name: str = "provider"

    def score_candidates(self, candidates: Sequence[Song], profile: PlaylistProfile) -> Dict[str, float]:
        raise NotImplementedError


def _cosine_similarity(vecs: np.ndarray, query: np.ndarray) -> np.ndarray:
    if vecs.size == 0:
        return np.array([])
    query_norm = np.linalg.norm(query)
    if query_norm < 1e-10:
        return np.zeros(vecs.shape[0])
    vec_norms = np.linalg.norm(vecs, axis=1)
    vec_norms[vec_norms == 0] = 1e-10
    scores = np.dot(vecs, query) / (vec_norms * query_norm)
    return scores


class LocalEmbeddingProvider(ScoreProvider):
    name = "local"

    def __init__(self, db):
        self.db = db

    def _song_embedding(self, song: Song) -> Optional[np.ndarray]:
        if song.embedding is None:
            try:
                song.embedding = self.db.generate_embedding(song)
            except Exception as exc:
                logger.warning("Failed to generate embedding for %s: %s", song.id, exc)
                return None
        emb = np.array(song.embedding, dtype=float)
        if emb.ndim != 1:
            emb = emb.reshape(-1)
        return emb

    def _profile_embedding(self, profile: PlaylistProfile) -> Optional[np.ndarray]:
        if profile.embedding is not None:
            return profile.embedding

        vectors = []
        for song in profile.seed_songs:
            emb = self._song_embedding(song)
            if emb is not None:
                vectors.append(emb)

        if profile.query:
            query_song = Song(id="__query__", name=profile.query, artist="")
            emb = self._song_embedding(query_song)
            if emb is not None:
                vectors.append(emb)

        if not vectors:
            return None

        profile.embedding = np.mean(np.vstack(vectors), axis=0)
        return profile.embedding

    def score_candidates(self, candidates: Sequence[Song], profile: PlaylistProfile) -> Dict[str, float]:
        profile_vec = self._profile_embedding(profile)
        if profile_vec is None:
            return {}

        embeddings = []
        song_ids = []
        for song in candidates:
            emb = self._song_embedding(song)
            if emb is None:
                continue
            embeddings.append(emb)
            song_ids.append(song.id)

        if not embeddings:
            return {}

        matrix = np.vstack(embeddings)
        scores = _cosine_similarity(matrix, profile_vec)
        return {song_id: float(score) for song_id, score in zip(song_ids, scores)}


class SpotifyAudioFeaturesProvider(ScoreProvider):
    name = "spotify_audio"

    FEATURE_KEYS = [
        "danceability",
        "energy",
        "speechiness",
        "acousticness",
        "instrumentalness",
        "liveness",
        "valence",
        "tempo",
    ]
    TEMPO_MAX = 200.0

    def __init__(self, spotify_manager):
        self.spotify = spotify_manager
        self._feature_cache: Dict[str, np.ndarray] = {}

    def _features_to_vector(self, features: dict) -> Optional[np.ndarray]:
        if not features:
            return None
        values = []
        for key in self.FEATURE_KEYS:
            value = features.get(key)
            if value is None:
                return None
            if key == "tempo":
                value = min(float(value) / self.TEMPO_MAX, 1.0)
            values.append(float(value))
        return np.array(values, dtype=float)

    def _ensure_uri(self, song: Song) -> Optional[str]:
        if song.spotify_uri:
            return song.spotify_uri
        if not self.spotify or not hasattr(self.spotify, "search_song"):
            return None
        try:
            song.spotify_uri = self.spotify.search_song(song)
        except Exception as exc:
            logger.warning("Spotify search failed for %s: %s", song.id, exc)
        return song.spotify_uri

    def _fetch_audio_features(self, uris: List[str]) -> Dict[str, np.ndarray]:
        if not uris:
            return {}
        if not self.spotify or not hasattr(self.spotify, "sp"):
            return {}

        results: Dict[str, np.ndarray] = {}
        batch_size = 100
        for i in range(0, len(uris), batch_size):
            batch = uris[i:i + batch_size]
            try:
                features_list = self.spotify.sp.audio_features(batch)
            except Exception as exc:
                logger.warning("Spotify audio_features failed: %s", exc)
                continue
            if not features_list:
                continue
            for uri, features in zip(batch, features_list):
                vector = self._features_to_vector(features or {})
                if vector is not None:
                    results[uri] = vector
        return results

    def _profile_vector(self, profile: PlaylistProfile) -> Optional[np.ndarray]:
        if profile.audio_vector is not None:
            return profile.audio_vector

        seed_uris = []
        for song in profile.seed_songs:
            uri = self._ensure_uri(song)
            if uri:
                seed_uris.append(uri)
        if not seed_uris:
            return None

        missing = [uri for uri in seed_uris if uri not in self._feature_cache]
        if missing:
            self._feature_cache.update(self._fetch_audio_features(missing))

        vectors = [self._feature_cache[uri] for uri in seed_uris if uri in self._feature_cache]
        if not vectors:
            return None

        profile.audio_vector = np.mean(np.vstack(vectors), axis=0)
        return profile.audio_vector

    def score_candidates(self, candidates: Sequence[Song], profile: PlaylistProfile) -> Dict[str, float]:
        profile_vec = self._profile_vector(profile)
        if profile_vec is None:
            return {}

        uris = []
        song_map: Dict[str, str] = {}
        for song in candidates:
            uri = self._ensure_uri(song)
            if uri:
                song_map[song.id] = uri
                if uri not in self._feature_cache:
                    uris.append(uri)

        if uris:
            self._feature_cache.update(self._fetch_audio_features(uris))

        scores: Dict[str, float] = {}
        for song_id, uri in song_map.items():
            vector = self._feature_cache.get(uri)
            if vector is None:
                continue
            score = _cosine_similarity(vector.reshape(1, -1), profile_vec)[0]
            scores[song_id] = float(score)

        return scores


class WebSearchScoreProvider(ScoreProvider):
    name = "web"

    def __init__(self, commands: Dict[str, str], timeout_sec: int = 60, max_candidates: Optional[int] = None):
        self.commands = commands
        self.timeout_sec = timeout_sec
        self.max_candidates = max_candidates

    def _build_payload(self, candidates: Sequence[Song], profile: PlaylistProfile) -> dict:
        return {
            "playlist_name": profile.playlist_name,
            "query": profile.query,
            "seed_text": profile.seed_text,
            "seed_songs": [
                {"id": song.id, "name": song.name, "artist": song.artist}
                for song in profile.seed_songs
            ],
            "candidates": [
                {"id": song.id, "name": song.name, "artist": song.artist}
                for song in candidates
            ],
            "requested_at": datetime.utcnow().isoformat() + "Z",
            "instructions": (
                "Use web search to judge how well each candidate fits the playlist theme. "
                "Return JSON with a 'scores' object mapping song id to a 0-1 score."
            ),
        }

    def _run_command(self, label: str, command: str, payload: dict) -> Dict[str, float]:
        try:
            args = shlex.split(command)
        except ValueError as exc:
            logger.warning("Invalid command for %s: %s", label, exc)
            return {}

        input_text = json.dumps(payload)
        is_codex_cli = label == "codex" and self._is_codex_cli(args)
        if is_codex_cli:
            args, input_text = self._prepare_codex_command(args, payload)

        try:
            result = subprocess.run(
                args,
                input=input_text,
                text=True,
                capture_output=True,
                timeout=self.timeout_sec,
            )
        except Exception as exc:
            logger.warning("Web scoring command failed for %s: %s", label, exc)
            return {}

        if result.returncode != 0:
            logger.warning("Web scoring command for %s exited with %s", label, result.returncode)
            if result.stderr:
                logger.warning("%s stderr: %s", label, result.stderr.strip())
                if is_codex_cli and self._stderr_needs_tty(result.stderr):
                    logger.info("Retrying %s with codex exec (non-interactive)", label)
                    return self._run_command(label, "codex exec -", payload)
                if is_codex_cli and self._stderr_unknown_argument(result.stderr):
                    flag = self._stderr_unknown_argument_flag(result.stderr)
                    if flag:
                        logger.info("Retrying %s without unsupported flag %s", label, flag)
                        if flag == "--search" and os.getenv("OPENAI_API_KEY"):
                            wrapper_cmd = _default_openai_web_score_command()
                            if wrapper_cmd != command:
                                logger.info(
                                    "Codex CLI does not support --search; falling back to OpenAI web scoring wrapper."
                                )
                                return self._run_command(label, wrapper_cmd, payload)
                        stripped = self._strip_flag(args, flag, takes_value=(flag == "--output-schema"))
                        return self._run_command(label, " ".join(stripped), payload)
                    logger.info("Retrying %s without unsupported codex flags", label)
                    stripped = self._strip_flag(args, "--search", takes_value=False)
                    return self._run_command(label, " ".join(stripped), payload)
            return {}

        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            logger.warning("Web scoring command for %s returned invalid JSON: %s", label, exc)
            return {}

        scores = output.get("scores")
        if not isinstance(scores, dict):
            return {}

        cleaned: Dict[str, float] = {}
        for key, value in scores.items():
            try:
                cleaned[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return cleaned

    def _prepare_codex_command(self, args: List[str], payload: dict) -> Tuple[List[str], str]:
        prompt = (
            "Use web search to judge how well each candidate fits the playlist theme. "
            "Return JSON with a 'scores' object mapping song id to a 0-1 score.\n\n"
            "Input JSON:\n"
            f"{json.dumps(payload, indent=2)}\n\n"
            "Return JSON only."
        )
        if args and args[0] == "codex" and "exec" not in args:
            args = ["codex", "exec", "--search", "-"]
        elif args and args[0] == "codex" and "exec" in args and "-" not in args:
            args = args + ["-"]
        return args, prompt

    @staticmethod
    def _is_codex_cli(args: List[str]) -> bool:
        if not args:
            return False
        return Path(args[0]).name == "codex"

    @staticmethod
    def _stderr_needs_tty(stderr: str) -> bool:
        lowered = (stderr or "").lower()
        return "stdin is not a terminal" in lowered

    @staticmethod
    def _stderr_unknown_argument(stderr: str) -> bool:
        lowered = (stderr or "").lower()
        return "unexpected argument" in lowered

    @staticmethod
    def _stderr_unknown_argument_flag(stderr: str) -> Optional[str]:
        match = re.search(r"unexpected argument '([^']+)'", stderr, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _strip_flag(args: List[str], flag: str, takes_value: bool) -> List[str]:
        if flag not in args:
            return args
        cleaned: List[str] = []
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if arg == flag:
                if takes_value:
                    skip_next = True
                continue
            cleaned.append(arg)
        return cleaned

    def score_candidates(self, candidates: Sequence[Song], profile: PlaylistProfile) -> Dict[str, float]:
        if not self.commands:
            return {}

        if self.max_candidates and len(candidates) > self.max_candidates:
            candidates = list(candidates)[: self.max_candidates]

        payload = self._build_payload(candidates, profile)
        collected: List[Dict[str, float]] = []
        for label, command in self.commands.items():
            scores = self._run_command(label, command, payload)
            if scores:
                collected.append(scores)

        if not collected:
            return {}

        combined: Dict[str, float] = {}
        for scores in collected:
            for song_id, score in scores.items():
                combined.setdefault(song_id, 0.0)
                combined[song_id] += score

        divisor = float(len(collected))
        return {song_id: score / divisor for song_id, score in combined.items()}


def _default_openai_web_score_command() -> str:
    script_path = Path(__file__).resolve().with_name("openai_web_score_wrapper.py")
    return shlex.join([sys.executable, str(script_path)])


class ScorePipeline:
    def __init__(self, providers: Sequence[ScoreProvider], weights: Optional[Dict[str, float]] = None):
        self.providers = list(providers)
        self.weights = weights or {}

    def _normalize(self, scores: Dict[str, float]) -> Dict[str, float]:
        if not scores:
            return {}
        values = list(scores.values())
        min_val = min(values)
        max_val = max(values)
        if max_val - min_val < 1e-9:
            return {key: 0.0 for key in scores}
        return {key: (value - min_val) / (max_val - min_val) for key, value in scores.items()}

    def score(self, candidates: Sequence[Song], profile: PlaylistProfile) -> Dict[str, float]:
        provider_scores: Dict[str, Dict[str, float]] = {}
        for provider in self.providers:
            scores = provider.score_candidates(candidates, profile)
            if scores:
                provider_scores[provider.name] = self._normalize(scores)

        if not provider_scores:
            return {}

        active_providers = list(provider_scores.keys())
        weights = {name: self.weights.get(name, 1.0) for name in active_providers}
        total_weight = sum(weights.values())
        if total_weight == 0:
            return {}

        combined: Dict[str, float] = {}
        for name, scores in provider_scores.items():
            weight = weights[name]
            for song in candidates:
                combined.setdefault(song.id, 0.0)
                combined[song.id] += weight * scores.get(song.id, 0.0)

        return {song_id: score / total_weight for song_id, score in combined.items()}


class MatchScorer:
    def __init__(self, playlist_name: str, db, spotify, history, config: ScoreConfig):
        self.playlist_name = playlist_name
        self.db = db
        self.spotify = spotify
        self.history = history
        self.config = config

    def _seed_songs(self) -> List[Song]:
        if not self.history or not self.history.generations:
            return []
        seeds: List[Song] = []
        seen = set()
        generations = self.history.generations[-max(1, self.config.seed_generations):]
        for generation in generations:
            for song_id in generation:
                if song_id in seen:
                    continue
                song = self.db.get_song_by_id(song_id)
                if song:
                    seeds.append(song)
                    seen.add(song_id)
                if len(seeds) >= self.config.seed_limit:
                    return seeds
        return seeds

    def _seed_text(self, seeds: Sequence[Song]) -> str:
        parts = []
        if self.config.query:
            parts.append(self.config.query)
        if self.playlist_name:
            parts.append(self.playlist_name)
        if seeds:
            song_snippets = [f"{song.name} by {song.artist}" for song in seeds[:20]]
            parts.append("; ".join(song_snippets))
        return " | ".join(parts)

    def _profile(self) -> PlaylistProfile:
        seeds = self._seed_songs()
        if not seeds and not self.config.query:
            profile_query = self.playlist_name
        else:
            profile_query = self.config.query
        seed_text = self._seed_text(seeds)
        return PlaylistProfile(
            playlist_name=self.playlist_name,
            query=profile_query,
            seed_songs=seeds,
            seed_text=seed_text,
        )

    def _web_commands(self) -> Dict[str, str]:
        commands: Dict[str, str] = {}
        cmd = os.getenv("WEB_SCORE_CMD")
        if cmd:
            commands["web"] = cmd
        claude = os.getenv("WEB_SCORE_CLAUDE_CMD")
        if claude:
            commands["claude"] = claude
        codex = os.getenv("WEB_SCORE_CODEX_CMD")
        if codex:
            commands["codex"] = codex
        if not commands and os.getenv("OPENAI_API_KEY"):
            commands["codex"] = _default_openai_web_score_command()
        return commands

    def _providers(self) -> Sequence[ScoreProvider]:
        strategy = (self.config.strategy or "").lower()
        providers: List[ScoreProvider] = []

        if strategy in {"local", "hybrid"}:
            providers.append(LocalEmbeddingProvider(self.db))

        if strategy == "hybrid":
            if self.spotify is not None:
                providers.append(SpotifyAudioFeaturesProvider(self.spotify))
            else:
                logger.info("Spotify audio features unavailable; skipping audio scoring.")
            commands = self._web_commands()
            if commands:
                providers.append(WebSearchScoreProvider(
                    commands,
                    timeout_sec=self.config.web_timeout_sec,
                    max_candidates=self.config.web_max_candidates
                ))
            else:
                logger.info("No web scoring command configured; skipping web scoring.")

        if strategy == "web":
            commands = self._web_commands()
            if commands:
                providers.append(WebSearchScoreProvider(
                    commands,
                    timeout_sec=self.config.web_timeout_sec,
                    max_candidates=self.config.web_max_candidates
                ))
            else:
                logger.info("No web scoring command configured; skipping web scoring.")

        return providers

    def score_candidates(self, candidates: Sequence[Song]) -> Dict[str, float]:
        profile = self._profile()
        providers = self._providers()
        if not providers:
            return {}

        pipeline = ScorePipeline(providers)

        return pipeline.score(candidates, profile)
