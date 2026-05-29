from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

from openai import OpenAI

from llm_json import (
    is_tool_type_error as _is_tool_type_error,
)
from llm_json import (
    parse_json_output as _parse_json_output,
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
    model = os.getenv("WEB_SCORE_MODEL") or os.getenv("WEB_SEARCH_MODEL") or "gpt-5.2"
    tool_type = os.getenv("WEB_SCORE_TOOL", os.getenv("WEB_SEARCH_TOOL", "web_search"))
    tool_choice = os.getenv("WEB_SCORE_TOOL_CHOICE", "").strip().lower()

    try:
        _timeout = float(os.getenv("WEB_SCORE_TIMEOUT", "120"))
    except ValueError:
        _timeout = 120.0
    client = OpenAI(timeout=_timeout)
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
    if not output_text:
        print("Warning: empty response from OpenAI API", file=sys.stderr)
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
        content = (
            getattr(item, "content", None) or item.get("content")
            if isinstance(item, dict)
            else None
        )
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


if __name__ == "__main__":
    sys.exit(main())
