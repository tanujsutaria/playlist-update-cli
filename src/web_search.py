from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
import importlib.util
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS = (
    "You are a music research assistant. Use web search to find new songs that match the user's "
    "criteria. Follow the provided source_policy (tiers, requirements) and any constraints "
    "(e.g., max monthly listeners). Return JSON only with a top-level 'summary' and a 'results' "
    "list. The summary should explain why these recommendations fit the user's criteria. Each "
    "result must include: song, artist, year (if known), why (short rationale), sources (list of "
    "URLs or objects with url/title/snippet), and metrics (object) for any user-requested metrics. If the query implies similarity "
    "(e.g., 'like X'), include a 'similarity' metric (0-1). If the query includes monthly listeners "
    "constraints, include a 'monthly_listeners' metric and cite sources. Optionally include a "
    "score 0-1 indicating fit confidence. If you can find a Spotify URL, include it as "
    "'spotify_url'. Additionally include a 'context' object with either a 'fields' list or "
    "keyed attributes (moods, genres, instrumentation, comparisons, era, themes). Each field "
    "should include its sources and a confidence score; mark strict=true only when sourced."
)

KNOWN_METRICS = {
    "bpm": ["bpm", "tempo"],
    "energy": ["energy", "energetic"],
    "danceability": ["danceable", "danceability"],
    "valence": ["valence", "happy", "sad", "uplifting", "melancholy"],
    "acousticness": ["acoustic", "acousticness"],
    "instrumentalness": ["instrumental", "instrumentalness"],
    "liveness": ["live", "liveness"],
    "popularity": ["popularity", "mainstream", "underground", "obscure"],
    "monthly_listeners": ["monthly listeners", "monthly listener", "listeners"],
    "release_year": ["year", "release year", "released", "era", "decade", "90s", "80s", "00s", "2010s"],
    "language": ["language", "spanish", "french", "german", "italian", "portuguese"],
    "region": ["region", "scene", "uk", "us", "japan", "korea", "brazil"],
    "mood": ["mood", "vibe", "atmospheric", "chill", "dark", "bright"],
    "genre": ["genre", "style"],
    "similarity": ["similar", "like", "in the style", "in the vein"],
}


def detect_search_commands(env: Optional[dict] = None) -> Dict[str, str]:
    env = env or os.environ
    commands: Dict[str, str] = {}

    claude_cmd = env.get("WEB_SEARCH_CLAUDE_CMD") or env.get("WEB_SCORE_CLAUDE_CMD")
    if claude_cmd:
        commands["claude"] = claude_cmd
    elif env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_API_KEY"):
        if _module_available("anthropic"):
            commands["claude"] = _default_anthropic_web_search_command()
        elif _command_exists("claude"):
            commands["claude"] = "claude --json"

    codex_cmd = env.get("WEB_SEARCH_CODEX_CMD") or env.get("WEB_SCORE_CODEX_CMD")
    if codex_cmd:
        commands["codex"] = codex_cmd
    elif env.get("OPENAI_API_KEY"):
        commands["codex"] = _default_openai_web_search_command()

    if not commands:
        generic_cmd = env.get("WEB_SEARCH_CMD") or env.get("WEB_SCORE_CMD")
        if generic_cmd:
            commands["web"] = generic_cmd

    return commands


def select_commands(commands: Dict[str, str], provider: str) -> Dict[str, str]:
    provider = (provider or "auto").lower()
    if provider in {"auto", "any"}:
        return commands
    if provider == "both":
        return {key: cmd for key, cmd in commands.items() if key in {"claude", "codex"}}
    if provider in commands:
        return {provider: commands[provider]}
    return {}


def extract_requested_metrics(query: str) -> List[str]:
    lowered = query.lower()
    tokens = set(re.findall(r"[a-z0-9']+", lowered))
    requested = []
    for metric, keywords in KNOWN_METRICS.items():
        for keyword in keywords:
            if " " in keyword:
                if keyword in lowered:
                    requested.append(metric)
                    break
            else:
                if keyword in tokens:
                    requested.append(metric)
                    break
    return requested


def extract_constraints(query: str) -> dict:
    lowered = query.lower()
    constraints: dict = {}

    max_match = re.search(
        r"(less than|under|below|at most|no more than)\s+([0-9][0-9,\.]*\s*[km]?)\s+monthly listeners",
        lowered,
    )
    if max_match:
        value = _parse_number(max_match.group(2))
        if value:
            constraints["max_monthly_listeners"] = value

    min_match = re.search(
        r"(more than|over|above|at least)\s+([0-9][0-9,\.]*\s*[km]?)\s+monthly listeners",
        lowered,
    )
    if min_match:
        value = _parse_number(min_match.group(2))
        if value:
            constraints["min_monthly_listeners"] = value

    if "similar" in lowered or "like " in lowered or "in the style" in lowered or "in the vein" in lowered:
        constraints["similarity_requested"] = True

    return constraints


def _parse_number(value: str) -> Optional[int]:
    if not value:
        return None
    cleaned = value.strip().lower().replace(",", "")
    multiplier = 1
    if cleaned.endswith("k"):
        multiplier = 1000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("m"):
        multiplier = 1000000
        cleaned = cleaned[:-1]
    try:
        return int(float(cleaned) * multiplier)
    except ValueError:
        return None


def extract_limit(query: str, default: int = 10, max_limit: int = 30) -> int:
    patterns = [
        r"\btop\s+(\d+)\b",
        r"\b(\d+)\s+(songs|tracks|results|recommendations)\b",
        r"\b(\d+)\s+new\s+(songs|tracks)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match:
            try:
                value = int(match.group(1))
                return max(1, min(max_limit, value))
            except ValueError:
                continue
    return default


def build_source_policy(expanded: bool) -> dict:
    policy = {
        "path": "hybrid",
        "min_sources": 2,
        "required_tiers": "Tier1>=1 OR (Tier2>=2)",
        "tiers": {
            "tier1": [
                "official artist/label sites",
                "MusicBrainz",
                "Discogs",
                "AllMusic",
            ],
            "tier2": [
                "reputable music press",
                "label rosters",
                "artist interviews",
                "festival lineups",
            ],
            "tier3": [
                "Bandcamp",
                "SoundCloud",
                "YouTube live sessions",
                "community blogs",
                "Reddit (low weight)",
            ],
        },
        "reject_if_only_tier3": True,
    }

    if expanded:
        policy["expanded"] = True
        policy["required_tiers"] = "Tier1>=1 OR Tier2>=1 OR Tier3>=2"
        policy["reject_if_only_tier3"] = False
    else:
        policy["expanded"] = False

    return policy


def build_search_payload(
    query: str,
    limit: int,
    requested_metrics: List[str],
    constraints: dict,
    expanded: bool,
) -> dict:
    return {
        "query": query,
        "limit": limit,
        "requested_at": datetime.utcnow().isoformat() + "Z",
        "instructions": DEFAULT_INSTRUCTIONS,
        "requested_metrics": requested_metrics,
        "constraints": constraints,
        "source_policy": build_source_policy(expanded),
        "expanded_search": expanded,
        "output_schema": {
            "results": [
                {
                    "song": "string",
                    "artist": "string",
                    "year": "string or int",
                    "why": "string",
                    "sources": ["url"],
                    "metrics": {"metric_name": "value"},
                    "score": "float 0-1 (optional)",
                    "spotify_url": "string (optional)",
                    "spotify_uri": "string (optional)",
                    "context": {
                        "fields": [
                            {
                                "field": "string (mood|genre|instrumentation|comparisons|era|themes|summary)",
                                "value": "string or list",
                                "sources": ["url"],
                                "confidence": "float 0-1",
                                "strict": "bool (true if sourced)"
                            }
                        ],
                        "moods": ["string"],
                        "genres": ["string"],
                        "instrumentation": ["string"],
                        "comparisons": ["string"],
                        "era": ["string"],
                        "themes": ["string"],
                        "sources": ["url"],
                        "confidence": "float 0-1"
                    },
                }
            ]
        },
    }


def _run_command(
    label: str,
    command: str,
    payload: dict,
    timeout_sec: int,
    env_overrides: Optional[dict] = None,
    allow_claude_cli_fallback: bool = True,
) -> Tuple[List[dict], str]:
    try:
        args = shlex.split(command)
    except ValueError as exc:
        logger.warning("Invalid search command for %s: %s", label, exc)
        return [], ""

    input_text = json.dumps(payload)
    is_codex_cli = label == "codex" and _is_codex_cli(args)
    if is_codex_cli:
        args, input_text = _prepare_codex_command(args, payload)

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    try:
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            env=env,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Search command for %s timed out after %ss.", label, timeout_sec)
        fallback_model = env.get("WEB_SEARCH_FALLBACK_MODEL", "").strip()
        if fallback_model and _is_openai_wrapper_command(command) and not env_overrides:
            logger.info("Retrying %s with fallback model %s.", label, fallback_model)
            return _run_command(
                label,
                command,
                payload,
                timeout_sec,
                env_overrides={"WEB_SEARCH_MODEL": fallback_model},
            )
        return [], ""
    except Exception as exc:
        logger.warning("Search command failed for %s: %s", label, exc)
        return [], ""

    if result.returncode != 0:
        logger.warning("Search command for %s exited with %s", label, result.returncode)
        if result.stderr:
            logger.warning("%s stderr: %s", label, result.stderr.strip())
            if "--json" in args and _stderr_has_unknown_json(result.stderr):
                logger.info("Retrying %s without --json flag", label)
                args = [arg for arg in args if arg != "--json"]
                return _run_command(
                    label,
                    " ".join(args),
                    payload,
                    timeout_sec,
                    env_overrides=env_overrides,
                    allow_claude_cli_fallback=allow_claude_cli_fallback,
                )
            if is_codex_cli and _stderr_needs_tty(result.stderr):
                logger.info("Retrying %s with codex exec (non-interactive)", label)
                args, input_text = _prepare_codex_command(["codex", "exec", "-"], payload)
                return _run_command(
                    label,
                    " ".join(args),
                    payload,
                    timeout_sec,
                    env_overrides=env_overrides,
                    allow_claude_cli_fallback=allow_claude_cli_fallback,
                )
            if is_codex_cli and _stderr_unknown_argument(result.stderr):
                flag = _stderr_unknown_argument_flag(result.stderr)
                if flag:
                    logger.info("Retrying %s without unsupported flag %s", label, flag)
                    if flag == "--search" and os.getenv("OPENAI_API_KEY"):
                        wrapper_cmd = _default_openai_web_search_command()
                        if wrapper_cmd != command:
                            logger.info(
                                "Codex CLI does not support --search; falling back to OpenAI web search wrapper."
                            )
                            return _run_command(
                                label,
                                wrapper_cmd,
                                payload,
                                timeout_sec,
                                env_overrides=env_overrides,
                                allow_claude_cli_fallback=allow_claude_cli_fallback,
                            )
                    args = _strip_flag(args, flag, takes_value=(flag == "--output-schema"))
                    if flag == "--search":
                        logger.warning("Codex CLI does not support --search; running without web search.")
                    return _run_command(
                        label,
                        " ".join(args),
                        payload,
                        timeout_sec,
                        env_overrides=env_overrides,
                        allow_claude_cli_fallback=allow_claude_cli_fallback,
                    )
                logger.info("Retrying %s without unsupported codex flags", label)
                args = _strip_flag(args, "--search", takes_value=False)
                args = _strip_flag(args, "--output-schema", takes_value=True)
                return _run_command(
                    label,
                    " ".join(args),
                    payload,
                    timeout_sec,
                    env_overrides=env_overrides,
                    allow_claude_cli_fallback=allow_claude_cli_fallback,
                )
        return [], ""

    output = _parse_json_output(result.stdout)
    if output is None:
        logger.warning("Search command for %s returned invalid JSON.", label)
        if label == "claude" and _should_retry_claude_with_wrapper(command):
            wrapper_cmd = _default_anthropic_web_search_command()
            if wrapper_cmd != command:
                logger.info("Retrying %s via Anthropic web search wrapper.", label)
                return _run_command(label, wrapper_cmd, payload, timeout_sec, env_overrides=env_overrides)
        if label == "claude" and not _module_available("anthropic"):
            logger.info(
                "Tip: set WEB_SEARCH_CLAUDE_CMD to 'python -m src.anthropic_web_search_wrapper' "
                "or install the anthropic package for structured JSON output."
            )
        return [], ""

    results, summary = _extract_output(output)
    if (
        label == "claude"
        and allow_claude_cli_fallback
        and _is_anthropic_wrapper_command(command)
        and not results
        and not summary
        and _command_exists("claude")
        and _env_truthy("WEB_SEARCH_CLAUDE_FALLBACK_CLI", default=True)
    ):
        logger.info("Claude wrapper returned empty output; retrying with claude CLI.")
        return _run_command(
            label,
            "claude --json",
            payload,
            timeout_sec,
            env_overrides=env_overrides,
            allow_claude_cli_fallback=False,
        )
    return results, summary


def _prepare_codex_command(args: List[str], payload: dict) -> Tuple[List[str], str]:
    prompt = _build_prompt_from_payload(payload)
    if args and args[0] == "codex" and "exec" not in args:
        args = ["codex", "exec", "--search", "--output-schema", _codex_schema(), "-"]
    elif args and args[0] == "codex" and "exec" in args and "-" not in args:
        args = args + ["-"]
    return args, prompt


def _is_codex_cli(args: List[str]) -> bool:
    if not args:
        return False
    return Path(args[0]).name == "codex"


def _is_openai_wrapper_command(command: str) -> bool:
    lowered = command.lower()
    return "openai_web_search_wrapper" in lowered


def _is_anthropic_wrapper_command(command: str) -> bool:
    lowered = command.lower()
    return "anthropic_web_search_wrapper" in lowered


def _should_retry_claude_with_wrapper(command: str) -> bool:
    lowered = command.lower()
    if "anthropic_web_search_wrapper" in lowered:
        return False
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")):
        return False
    return _module_available("anthropic")


def _default_codex_command() -> str:
    schema = _codex_schema()
    return f'codex exec --search --output-schema {schema} -'


def _default_openai_web_search_command() -> str:
    script_path = Path(__file__).resolve().with_name("openai_web_search_wrapper.py")
    return shlex.join([sys.executable, str(script_path)])


def _default_anthropic_web_search_command() -> str:
    script_path = Path(__file__).resolve().with_name("anthropic_web_search_wrapper.py")
    return shlex.join([sys.executable, str(script_path)])


def _codex_schema() -> str:
    return (
        '{"type":"object","properties":{"summary":{"type":"string"},"results":{"type":"array","items":{"type":"object","properties":{"song":{"type":"string"},"artist":{"type":"string"},"year":{"type":["string","number"]},"why":{"type":"string"},"sources":{"type":"array","items":{"type":"string"}},"metrics":{"type":"object"},"score":{"type":["number","null"]},"spotify_url":{"type":["string","null"]},"spotify_uri":{"type":["string","null"]}},"required":["song","artist","sources","metrics"]}}},"required":["summary","results"]}'
    )


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}




def _build_prompt_from_payload(payload: dict) -> str:
    instructions = payload.get("instructions") or DEFAULT_INSTRUCTIONS
    filtered = {key: value for key, value in payload.items() if key != "instructions"}
    return (
        f"{instructions}\n\n"
        "Input JSON:\n"
        f"{json.dumps(filtered, indent=2)}\n\n"
        "Return JSON only."
    )


def _stderr_has_unknown_json(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return "unknown option" in lowered and "--json" in lowered


def _stderr_needs_tty(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return "stdin is not a terminal" in lowered


def _stderr_unknown_argument(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return "unexpected argument" in lowered


def _stderr_unknown_argument_flag(stderr: str) -> Optional[str]:
    match = re.search(r"unexpected argument '([^']+)'", stderr, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _parse_json_output(text: str) -> Optional[object]:
    if text:
        candidate = text.strip()
        if candidate.startswith("```json"):
            candidate = candidate[len("```json") :]
        if candidate.startswith("```"):
            candidate = candidate[len("```") :]
        if candidate.endswith("```"):
            candidate = candidate[: -3]
        candidate = candidate.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            parsed = _try_parse_json(candidate)
            if parsed is not None:
                return parsed
        if candidate.startswith("[") and candidate.endswith("]"):
            parsed = _try_parse_json(candidate)
            if parsed is not None:
                return parsed

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```json\\s*([\\s\\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return parsed

    fenced = re.search(r"```\\s*([\\s\\S]*?)```", text)
    if fenced:
        candidate = fenced.group(1).strip()
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return parsed

    brace_match = _extract_json_block(text, "{", "}")
    if brace_match:
        parsed = _try_parse_json(brace_match)
        if parsed is not None:
            return parsed

    bracket_match = _extract_json_block(text, "[", "]")
    if bracket_match:
        parsed = _try_parse_json(bracket_match)
        if parsed is not None:
            return parsed

    return None


def _try_parse_json(candidate: str) -> Optional[object]:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _extract_json_block(text: str, open_char: str, close_char: str) -> Optional[str]:
    start = text.find(open_char)
    end = text.rfind(close_char)
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1].strip()


def _extract_text_from_response(output: object) -> Optional[str]:
    if not isinstance(output, dict):
        return None
    output_text = output.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    completion = output.get("completion")
    if isinstance(completion, str) and completion.strip():
        return completion
    message = output.get("message")
    if isinstance(message, dict):
        nested = _extract_text_from_response(message)
        if nested:
            return nested
    content = output.get("content")
    if not isinstance(content, list):
        return None
    texts: List[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") in {"text", "output_text"} and block.get("text"):
                texts.append(str(block.get("text")))
            elif "text" in block and block.get("text"):
                texts.append(str(block.get("text")))
        else:
            text = getattr(block, "text", None)
            if text:
                texts.append(str(text))
    if texts:
        return "\n".join(texts)
    return None


def _strip_flag(args: List[str], flag: str, takes_value: bool) -> List[str]:
    if flag not in args:
        return args
    cleaned: List[str] = []
    skip_next = False
    for idx, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == flag:
            if takes_value:
                skip_next = True
            continue
        cleaned.append(arg)
    return cleaned


def _parse_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%s; using %s.", name, raw, default)
        return default


def _extract_output(output: object) -> Tuple[List[dict], str]:
    results: Iterable = []
    summary = ""
    if isinstance(output, dict):
        summary = str(output.get("summary") or output.get("overview") or output.get("rationale") or "")
        results = (
            output.get("results")
            or output.get("songs")
            or output.get("recommendations")
            or output.get("tracks")
            or []
        )
        if not results and not summary:
            extracted = _extract_text_from_response(output)
            if extracted:
                parsed = _parse_json_output(extracted)
                if parsed is not None and parsed is not output:
                    return _extract_output(parsed)
    elif isinstance(output, list):
        results = output
    else:
        return [], ""

    cleaned: List[dict] = []
    for item in results:
        normalized = _normalize_item(item)
        if normalized:
            cleaned.append(normalized)
    return cleaned, summary


def _normalize_item(item: object) -> Optional[dict]:
    if isinstance(item, str):
        song, artist = _split_song_artist(item)
        if not song or not artist:
            return None
        return {
            "song": song,
            "artist": artist,
            "year": "",
            "why": "",
            "sources": [],
            "score": None,
        }

    if not isinstance(item, dict):
        return None

    song = item.get("song") or item.get("title") or item.get("track") or item.get("name")
    artist = item.get("artist") or item.get("artist_name")

    if isinstance(song, dict):
        song_name = song.get("name") or song.get("title") or song.get("track") or song.get("song")
        if not artist:
            artist = (
                song.get("artist")
                or song.get("artist_name")
                or song.get("primary_artist")
                or song.get("by")
            )
        song = song_name or song

    if not artist:
        artists = item.get("artists")
        if isinstance(artists, list) and artists:
            first = artists[0]
            if isinstance(first, dict):
                artist = first.get("name") or first.get("artist")
            else:
                artist = first
        elif isinstance(artists, dict):
            artist = artists.get("name") or artists.get("artist")

    if isinstance(artist, dict):
        artist = artist.get("name") or artist.get("artist") or artist.get("primary_artist")

    if not artist and isinstance(song, str):
        song, artist = _split_song_artist(song)

    if not song or not artist:
        return None

    sources = item.get("sources") or item.get("source_urls") or item.get("links") or []
    if isinstance(sources, dict):
        sources = [sources]
    if isinstance(sources, str):
        sources = [sources]
    source_details: List[dict] = []
    normalized_sources: List[str] = []
    for source in sources:
        if isinstance(source, dict):
            url = source.get("url") or source.get("link") or source.get("source") or source.get("href")
            if url:
                normalized_sources.append(str(url))
                detail = {"url": str(url)}
                title = source.get("title") or source.get("name")
                snippet = source.get("snippet") or source.get("summary") or source.get("description")
                if title:
                    detail["title"] = str(title)
                if snippet:
                    detail["snippet"] = str(snippet)
                source_details.append(detail)
        else:
            if source:
                normalized_sources.append(str(source))

    metrics = {}
    if isinstance(item.get("metrics"), dict):
        metrics.update(item.get("metrics"))
    for key in [
        "bpm",
        "tempo",
        "energy",
        "danceability",
        "valence",
        "acousticness",
        "instrumentalness",
        "liveness",
        "popularity",
        "monthly_listeners",
        "genre",
        "mood",
        "language",
        "region",
        "similarity",
        "key",
        "mode",
    ]:
        if key in item and item[key] is not None:
            metrics[key] = item[key]

    return {
        "song": str(song),
        "artist": str(artist),
        "year": item.get("year") or item.get("release_year") or item.get("released") or "",
        "why": item.get("why") or item.get("reason") or item.get("rationale") or item.get("notes") or "",
        "sources": normalized_sources,
        "source_details": source_details,
        "metrics": metrics,
        "score": _safe_float(item.get("score") or item.get("confidence")),
        "spotify_url": item.get("spotify_url") or item.get("spotify") or "",
        "spotify_uri": item.get("spotify_uri") or item.get("uri") or "",
        "context": item.get("context") if isinstance(item.get("context"), dict) else {},
    }


def _split_song_artist(value: str) -> Tuple[str, str]:
    if " by " in value.lower():
        parts = value.split(" by ")
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip()
    if " - " in value:
        parts = value.split(" - ")
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip()
    return value.strip(), ""


def _safe_float(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def synthesize_results(provider_results: Dict[str, List[dict]], limit: int) -> List[dict]:
    combined: Dict[str, dict] = {}
    order: List[str] = []

    for provider, results in provider_results.items():
        for idx, item in enumerate(results):
            key = f"{item['artist'].lower()}|||{item['song'].lower()}"
            if key not in combined:
                combined[key] = {
                    "song": item["song"],
                    "artist": item["artist"],
                    "year": item.get("year") or "",
                    "why": [],
                    "sources": set(),
                    "source_details": {},
                    "metrics": {},
                    "providers": [],
                    "scores": [],
                    "context": {},
                    "first_seen": len(order),
                }
                order.append(key)

            if item.get("year") and not combined[key]["year"]:
                combined[key]["year"] = item["year"]
            if item.get("why"):
                combined[key]["why"].append(f"{provider}: {item['why']}")
            combined[key]["providers"].append(provider)
            combined[key]["sources"].update(item.get("sources") or [])
            details = item.get("source_details") or []
            if isinstance(details, list):
                for detail in details:
                    if not isinstance(detail, dict):
                        continue
                    url = detail.get("url")
                    if not url:
                        continue
                    existing_detail = combined[key]["source_details"].get(url) or {}
                    if not existing_detail:
                        combined[key]["source_details"][url] = detail
                        continue
                    if detail.get("title") and not existing_detail.get("title"):
                        existing_detail["title"] = detail.get("title")
                    if detail.get("snippet") and not existing_detail.get("snippet"):
                        existing_detail["snippet"] = detail.get("snippet")
                    combined[key]["source_details"][url] = existing_detail
            metrics = item.get("metrics") or {}
            if isinstance(metrics, dict):
                for metric_key, metric_value in metrics.items():
                    if metric_key not in combined[key]["metrics"]:
                        combined[key]["metrics"][metric_key] = metric_value
            if item.get("score") is not None:
                combined[key]["scores"].append(item["score"])

            context = item.get("context") or {}
            if isinstance(context, dict) and context:
                existing = combined[key].get("context") or {}
                if not existing:
                    combined[key]["context"] = context
                else:
                    if isinstance(existing.get("fields"), list) and isinstance(context.get("fields"), list):
                        existing_fields = existing.get("fields") or []
                        existing_fields.extend(context.get("fields") or [])
                        existing["fields"] = existing_fields
                    for key_name in ("moods", "genres", "instrumentation", "comparisons", "era", "themes", "sources"):
                        if context.get(key_name):
                            existing.setdefault(key_name, [])
                            existing[key_name] = list({
                                *existing.get(key_name, []),
                                *context.get(key_name, []),
                            })
                    if context.get("confidence") and not existing.get("confidence"):
                        existing["confidence"] = context.get("confidence")
                    combined[key]["context"] = existing

    results: List[dict] = []
    for key, entry in combined.items():
        mentions = len(set(entry["providers"]))
        avg_score = None
        if entry["scores"]:
            avg_score = sum(entry["scores"]) / len(entry["scores"])
        results.append(
            {
                "song": entry["song"],
                "artist": entry["artist"],
                "year": entry["year"],
                "why": " | ".join(entry["why"]).strip(),
                "sources": sorted(entry["sources"]),
                "source_details": list(entry["source_details"].values()),
                "metrics": entry["metrics"],
                "providers": sorted(set(entry["providers"])),
                "mentions": mentions,
                "score": avg_score,
                "context": entry.get("context") or {},
                "_rank": (mentions, avg_score or 0.0, -entry["first_seen"]),
            }
        )

    results.sort(key=lambda item: (item["_rank"][0], item["_rank"][1], item["_rank"][2]), reverse=True)
    trimmed = results[:limit] if limit else results
    for item in trimmed:
        item.pop("_rank", None)
    return trimmed


def run_deep_search(
    query: str,
    limit: Optional[int] = None,
    provider: str = "auto",
    timeout_sec: int = 120,
    expanded: bool = False,
) -> Tuple[List[dict], Dict[str, List[dict]], List[str], Optional[str], List[str], str, dict, dict]:
    commands = detect_search_commands()
    selected = select_commands(commands, provider)
    if not selected:
        return [], {}, [], "No search providers configured.", [], "", {}, build_source_policy(expanded)

    env_timeout = os.getenv("WEB_SEARCH_TIMEOUT_SEC")
    if env_timeout:
        try:
            timeout_sec = int(env_timeout)
        except ValueError:
            logger.warning("Invalid WEB_SEARCH_TIMEOUT_SEC=%s; using default.", env_timeout)
    else:
        model_hint = (os.getenv("WEB_SEARCH_MODEL") or "").lower()
        if "deep" in model_hint and timeout_sec < 1200:
            timeout_sec = 1200

    resolved_limit = limit if limit is not None else extract_limit(query)
    if expanded:
        resolved_limit = min(30, resolved_limit + 5)
    constraints = extract_constraints(query)
    requested_metrics = extract_requested_metrics(query)
    if constraints.get("max_monthly_listeners") or constraints.get("min_monthly_listeners"):
        if "monthly_listeners" not in requested_metrics:
            requested_metrics.append("monthly_listeners")
    if constraints.get("similarity_requested") and "similarity" not in requested_metrics:
        requested_metrics.append("similarity")
    logger.info(
        "Deep search started (expanded=%s, limit=%s).",
        "yes" if expanded else "no",
        resolved_limit,
    )
    logger.info("Query: %s", query)
    logger.info("Using providers: %s", ", ".join(selected.keys()))
    logger.info("Each provider may take up to %ss.", timeout_sec)
    payload = build_search_payload(query, resolved_limit, requested_metrics, constraints, expanded)
    provider_results: Dict[str, List[dict]] = {label: [] for label in selected.keys()}
    provider_summaries: Dict[str, str] = {}
    started_at = time.monotonic()

    parallel_per_provider = _parse_env_int("WEB_SEARCH_PARALLEL_PER_PROVIDER", default=5)
    if parallel_per_provider < 1:
        parallel_per_provider = 1
    total_runs = parallel_per_provider * len(selected)
    if total_runs > 1:
        logger.info(
            "Launching %s parallel searches (%s per provider).",
            total_runs,
            parallel_per_provider,
        )

    future_to_meta = {}
    with ThreadPoolExecutor(max_workers=total_runs) as executor:
        for label, command in selected.items():
            logger.info("Submitting %s parallel searches for %s.", parallel_per_provider, label)
            for idx in range(parallel_per_provider):
                started = time.monotonic()
                future = executor.submit(_run_command, label, command, payload, timeout_sec)
                future_to_meta[future] = (label, idx, started)
        for future in as_completed(future_to_meta):
            label, idx, started = future_to_meta[future]
            try:
                results, summary = future.result()
            except Exception as exc:
                logger.warning("Search run %s-%s failed: %s", label, idx + 1, exc)
                continue
            elapsed = time.monotonic() - started
            logger.info(
                "%s run %s/%s finished in %.1fs (%s results).",
                label,
                idx + 1,
                parallel_per_provider,
                elapsed,
                len(results),
            )
            if results:
                provider_results[label].extend(results)
            if summary:
                if label in provider_summaries and summary not in provider_summaries[label]:
                    provider_summaries[label] = f"{provider_summaries[label]} | {summary}"
                else:
                    provider_summaries[label] = summary

    filtered_results = {label: results for label, results in provider_results.items() if results}
    if not filtered_results:
        return [], {}, [], "No results returned by providers.", requested_metrics, "", constraints, build_source_policy(expanded)

    logger.info("Synthesizing results across providers...")
    combined = synthesize_results(filtered_results, resolved_limit)
    total_elapsed = time.monotonic() - started_at
    logger.info("Deep search complete (%s results, %.1fs).", len(combined), total_elapsed)
    providers = list(filtered_results.keys())
    filtered_summaries = {label: summary for label, summary in provider_summaries.items() if label in filtered_results}
    summary = synthesize_summary(filtered_summaries, query)
    return combined, filtered_results, providers, None, requested_metrics, summary, constraints, build_source_policy(expanded)


def synthesize_summary(provider_summaries: Dict[str, str], query: str) -> str:
    if not provider_summaries:
        return f"Recommendations align with: {query}"
    if len(provider_summaries) == 1:
        return next(iter(provider_summaries.values()))
    parts = []
    for provider, summary in provider_summaries.items():
        parts.append(f"{provider}: {summary}")
    return " | ".join(parts)
