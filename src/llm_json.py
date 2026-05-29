"""Shared helpers for parsing JSON out of LLM text responses.

Extracted from web_search.py and the provider wrapper scripts, which each
carried near-identical copies. Provider-specific output extraction
(_extract_output_text) stays in the individual wrappers.
"""

from __future__ import annotations

import json
import re
from typing import Optional


def try_parse_json(candidate: str) -> Optional[object]:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def extract_json_block(text: str, open_char: str, close_char: str) -> Optional[str]:
    start = text.find(open_char)
    end = text.rfind(close_char)
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1].strip()


def strip_fence(text: str) -> Optional[str]:
    candidate = text.strip()
    if candidate.startswith("```json"):
        candidate = candidate[len("```json") :]
    if candidate.startswith("```"):
        candidate = candidate[len("```") :]
    if candidate.endswith("```"):
        candidate = candidate[:-3]
    candidate = candidate.strip()
    if (candidate.startswith("{") and candidate.endswith("}")) or (
        candidate.startswith("[") and candidate.endswith("]")
    ):
        return candidate
    return None


def parse_json_output(text: str) -> Optional[object]:
    if not text:
        return None
    candidate = strip_fence(text)
    if candidate:
        parsed = try_parse_json(candidate)
        if parsed is not None:
            return parsed

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```json\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        parsed = try_parse_json(fenced.group(1).strip())
        if parsed is not None:
            return parsed

    fenced = re.search(r"```\s*([\s\S]*?)```", text)
    if fenced:
        parsed = try_parse_json(fenced.group(1).strip())
        if parsed is not None:
            return parsed

    brace_match = extract_json_block(text, "{", "}")
    if brace_match:
        parsed = try_parse_json(brace_match)
        if parsed is not None:
            return parsed

    bracket_match = extract_json_block(text, "[", "]")
    if bracket_match:
        parsed = try_parse_json(bracket_match)
        if parsed is not None:
            return parsed

    return None


def is_tool_type_error(exc: Exception, tool_type: str) -> bool:
    message = str(exc).lower()
    return (
        "tool" in message
        and tool_type in message
        and ("invalid" in message or "unsupported" in message)
    )
