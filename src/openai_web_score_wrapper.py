from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

from openai import OpenAI


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
    model = os.getenv("WEB_SCORE_MODEL") or os.getenv("WEB_SEARCH_MODEL") or "gpt-4o"
    tool_type = os.getenv("WEB_SCORE_TOOL", os.getenv("WEB_SEARCH_TOOL", "web_search"))
    tool_choice = os.getenv("WEB_SCORE_TOOL_CHOICE", "").strip().lower()

    client = OpenAI()
    request: Dict[str, Any] = {
        "model": model,
        "input": prompt,
        "tools": [{"type": tool_type}],
    }
    if tool_choice in {"auto", "required", "none"}:
        request["tool_choice"] = tool_choice

    try:
        response = client.responses.create(**request)
    except Exception as exc:
        if _is_tool_type_error(exc, tool_type) and tool_type == "web_search":
            request["tools"] = [{"type": "web_search_preview"}]
            try:
                response = client.responses.create(**request)
            except Exception as retry_exc:
                print(f"OpenAI API error: {retry_exc}", file=sys.stderr)
                return 1
        else:
            print(f"OpenAI API error: {exc}", file=sys.stderr)
            return 1

    output_text = getattr(response, "output_text", None) or _extract_output_text(response)
    parsed = _parse_json_output(output_text or "")
    if parsed is None:
        parsed = {"scores": {}}
    if isinstance(parsed, list):
        parsed = {"scores": {}}
    if isinstance(parsed, dict) and "scores" not in parsed:
        parsed = {"scores": {}}

    json.dump(parsed, sys.stdout)
    return 0


def _build_prompt(payload: dict) -> str:
    return (
        "Use web search to judge how well each candidate fits the playlist theme. "
        "Return JSON with a 'scores' object mapping song id to a 0-1 score.\n\n"
        "Input JSON:\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        "Return JSON only."
    )


def _extract_output_text(response: Any) -> str:
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        return ""
    texts: List[str] = []
    for item in output:
        content = getattr(item, "content", None) or item.get("content") if isinstance(item, dict) else None
        if not isinstance(content, list):
            continue
        for chunk in content:
            text = None
            if isinstance(chunk, dict):
                if chunk.get("type") == "output_text":
                    text = chunk.get("text")
                elif "text" in chunk:
                    text = chunk.get("text")
            else:
                text = getattr(chunk, "text", None)
            if text:
                texts.append(str(text))
    return "\n".join(texts)


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
    if candidate.startswith("{") and candidate.endswith("}"):
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
    return "tool" in message and tool_type in message and ("invalid" in message or "unsupported" in message)


if __name__ == "__main__":
    raise SystemExit(main())
