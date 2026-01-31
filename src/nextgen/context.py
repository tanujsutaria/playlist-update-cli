from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .extract import ExtractedContext, ExtractedField


@dataclass
class ContextCard:
    context_text: str
    strict_text: str
    lenient_text: str
    fields_json: str
    sources_json: str
    strict_ratio: float


def _token_count(text: str) -> int:
    return len(text.split())


def _format_field(field: ExtractedField) -> str:
    label = field.name.replace("_", " ").title()
    return f"{label}: {field.value}"


def build_context_card(
    song: str,
    artist: str,
    year: Optional[str],
    extracted: ExtractedContext,
    strict_threshold: float,
) -> ContextCard:
    strict_fields = [field for field in extracted.fields if field.strict]
    lenient_fields = [field for field in extracted.fields if not field.strict]

    strict_text = " ".join([_format_field(field) for field in strict_fields]).strip()
    lenient_text = " ".join([_format_field(field) for field in lenient_fields]).strip()

    strict_tokens = _token_count(strict_text)
    lenient_tokens = _token_count(lenient_text)

    include_lenient = strict_tokens == 0 or (
        lenient_tokens > 0 and (strict_tokens / (strict_tokens + lenient_tokens)) < strict_threshold
    )

    context_parts = [f"{song} by {artist}."] if song and artist else []
    if year:
        context_parts.append(f"Year: {year}.")
    if strict_text:
        context_parts.append(strict_text)
    if include_lenient and lenient_text:
        context_parts.append(lenient_text)

    context_text = " ".join(context_parts).strip()

    if include_lenient and (strict_tokens + lenient_tokens) > 0:
        strict_ratio = float(strict_tokens) / float(strict_tokens + lenient_tokens)
    elif strict_tokens > 0:
        strict_ratio = 1.0
    else:
        strict_ratio = 0.0

    fields_json = json.dumps([
        {
            "field": field.name,
            "value": field.value,
            "strict": field.strict,
            "confidence": field.confidence,
            "sources": field.sources,
        }
        for field in extracted.fields
    ])

    return ContextCard(
        context_text=context_text,
        strict_text=strict_text,
        lenient_text=lenient_text,
        fields_json=fields_json,
        sources_json=json.dumps(extracted.sources),
        strict_ratio=strict_ratio,
    )
