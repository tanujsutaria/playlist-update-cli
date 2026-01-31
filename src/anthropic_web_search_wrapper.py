from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

import anthropic

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
    max_uses = int(os.getenv("ANTHROPIC_WEB_SEARCH_MAX_USES", "5"))
    max_tokens = int(os.getenv("ANTHROPIC_WEB_SEARCH_MAX_TOKENS", "1024"))

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
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
    return (
        f"{instructions}\n\n"
        "Input JSON:\n"
        f"{json.dumps(trimmed, indent=2)}\n\n"
        "Return JSON only."
    )


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


def _parse_json_output(text: str) -> Optional[object]:
    if not text:
        return None
    candidate = _strip_fence(text)
    if candidate:
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return parsed

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```json\\s*([\\s\\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        parsed = _try_parse_json(fenced.group(1).strip())
        if parsed is not None:
            return parsed

    fenced = re.search(r"```\\s*([\\s\\S]*?)```", text)
    if fenced:
        parsed = _try_parse_json(fenced.group(1).strip())
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


def _strip_fence(text: str) -> Optional[str]:
    candidate = text.strip()
    if candidate.startswith("```json"):
        candidate = candidate[len("```json") :]
    if candidate.startswith("```"):
        candidate = candidate[len("```") :]
    if candidate.endswith("```"):
        candidate = candidate[: -3]
    candidate = candidate.strip()
    if (candidate.startswith("{") and candidate.endswith("}")) or (
        candidate.startswith("[") and candidate.endswith("]")
    ):
        return candidate
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


def _is_tool_type_error(exc: Exception, tool_type: str) -> bool:
    message = str(exc).lower()
    return "tool" in message and tool_type.replace("_", " ")[:8] in message and "invalid" in message


def _is_model_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "model" in message and ("not found" in message or "invalid" in message or "unknown" in message)


if __name__ == "__main__":
    raise SystemExit(main())
