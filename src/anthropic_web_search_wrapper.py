from __future__ import annotations

import json
import os
import sys
from typing import Any, List, Optional

import anthropic

from llm_json import (
    is_tool_type_error as _is_tool_type_error,
)
from llm_json import (
    parse_json_output as _parse_json_output,
)

DEFAULT_INSTRUCTIONS = (
    "You are a music research assistant. Use web search to find new songs that match the user's "
    "criteria. Follow the provided source_policy (tiers, requirements) and any constraints "
    "(e.g., max monthly listeners). Return JSON only with a top-level 'summary' and a 'results' "
    "list. The summary should explain why these recommendations fit the user's criteria. Each "
    "result must include: song, artist, year (if known), why (short rationale), sources (list of "
    "URLs), and metrics (object) for any user-requested metrics. If the query implies similarity "
    "(e.g., 'like X'), include a 'similarity' metric (0-1). If the query includes monthly listeners "
    "constraints, include a 'monthly_listeners' metric and cite sources. Optionally include a "
    "score 0-1 indicating fit confidence. If you can find a Spotify URL, include it as "
    "'spotify_url'."
)

# The pipeline's structured-JSON answers (a `results` list with per-track context)
# are long. A small cap silently truncates the response mid-object, so it fails to
# parse and the wrapper returns ZERO results — which then trips the broken
# `claude --json` CLI fallback. 4096 leaves room for a full results list.
DEFAULT_WEB_SEARCH_MAX_TOKENS = 4096


def _resolve_max_tokens(env: Optional[dict] = None) -> int:
    """Resolve ANTHROPIC_WEB_SEARCH_MAX_TOKENS, defaulting to 4096 (not 1024)."""
    source = env if env is not None else os.environ
    try:
        return int(
            source.get("ANTHROPIC_WEB_SEARCH_MAX_TOKENS", str(DEFAULT_WEB_SEARCH_MAX_TOKENS))
        )
    except (ValueError, TypeError):
        return DEFAULT_WEB_SEARCH_MAX_TOKENS


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("No input received on stdin.", file=sys.stderr)
        return 1

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON input: {exc}", file=sys.stderr)
        return 1

    prompt = _build_prompt(payload)
    model = (
        os.getenv("ANTHROPIC_WEB_SEARCH_MODEL")
        or os.getenv("ANTHROPIC_MODEL")
        or os.getenv("WEB_SEARCH_MODEL")
        or "claude-opus-4-5"
    )
    tool_type = os.getenv("ANTHROPIC_WEB_SEARCH_TOOL", "web_search_20250305")
    try:
        max_uses = int(os.getenv("ANTHROPIC_WEB_SEARCH_MAX_USES", "5"))
    except ValueError:
        max_uses = 5
    max_tokens = _resolve_max_tokens()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
        return 1

    client = anthropic.Anthropic(api_key=api_key)
    response = _run_with_fallbacks(
        client=client,
        models=_resolve_model_candidates(model),
        prompt=prompt,
        max_tokens=max_tokens,
        tool_type=tool_type,
        max_uses=max_uses,
    )

    if response is None:
        return 1

    output_text = _extract_output_text(response)
    if not output_text and _has_tool_use(response):
        fallback = _run_without_tools(
            client=client,
            models=_resolve_model_candidates(model),
            prompt=prompt,
            max_tokens=max_tokens,
        )
        if fallback is None:
            return 1
        response = fallback
        output_text = _extract_output_text(response)
    parsed = _parse_json_output(output_text or "")
    if parsed is None:
        summary = (output_text or "").strip()
        parsed = {"summary": summary[:2000], "results": []}
    elif isinstance(parsed, list):
        parsed = {"summary": "", "results": parsed}

    json.dump(parsed, sys.stdout)
    return 0


def _run_with_fallbacks(
    client: anthropic.Anthropic,
    models: List[str],
    prompt: str,
    max_tokens: int,
    tool_type: str,
    max_uses: int,
) -> Optional[object]:
    tool_types: List[Optional[str]] = []
    if tool_type:
        tool_types.append(tool_type)
    if tool_type != "web_search_20250305":
        tool_types.append("web_search_20250305")
    if tool_type != "web_search":
        tool_types.append("web_search")
    tool_types.append(None)

    for model in models:
        tool_failed = True
        for candidate in tool_types:
            try:
                if candidate:
                    tools = [{"type": candidate, "name": "web_search", "max_uses": max_uses}]
                    return client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        messages=[{"role": "user", "content": prompt}],
                        tools=tools,
                    )
                tool_failed = False
                return client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as exc:
                if _is_model_error(exc):
                    break
                if candidate and _is_tool_type_error(exc, candidate):
                    continue
                print(f"Anthropic API error: {exc}", file=sys.stderr)
                return None
        if not tool_failed:
            return None

    return None


def _run_without_tools(
    client: anthropic.Anthropic,
    models: List[str],
    prompt: str,
    max_tokens: int,
) -> Optional[object]:
    for model in models:
        try:
            return client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            if _is_model_error(exc):
                continue
            print(f"Anthropic API error: {exc}", file=sys.stderr)
            return None
    return None


def _resolve_model_candidates(primary: str) -> List[str]:
    candidates = [primary]
    extra = os.getenv("ANTHROPIC_WEB_SEARCH_MODEL_FALLBACKS", "").strip()
    if extra:
        for token in extra.split(","):
            name = token.strip()
            if name and name not in candidates:
                candidates.append(name)
    for fallback in ("claude-opus-4-1", "claude-sonnet-4-20250514"):
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def _build_prompt(payload: dict) -> str:
    instructions = payload.get("instructions") or DEFAULT_INSTRUCTIONS
    trimmed = {key: value for key, value in payload.items() if key != "instructions"}
    return f"{instructions}\n\nInput JSON:\n{json.dumps(trimmed, indent=2)}\n\nReturn JSON only."


def _extract_output_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return ""
    texts: List[str] = []
    for block in content:
        text = None
        if isinstance(block, dict):
            if block.get("type") == "text":
                text = block.get("text")
            elif "text" in block:
                text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if text:
            texts.append(str(text))
    return "\n".join(texts)


def _has_tool_use(message: Any) -> bool:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "tool_use":
                return True
        else:
            if getattr(block, "type", None) == "tool_use":
                return True
    return False


def _is_model_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "model" in message and (
        "not found" in message or "invalid" in message or "unknown" in message
    )


if __name__ == "__main__":
    sys.exit(main())
