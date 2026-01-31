from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class ExtractedField:
    name: str
    value: str
    strict: bool
    confidence: float
    sources: List[str]


@dataclass
class ExtractedContext:
    fields: List[ExtractedField]
    sources: List[str]


def _coerce_sources(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [str(value)]


def _field_from_value(
    name: str,
    value: Any,
    sources: Optional[Iterable[str]] = None,
    confidence: Optional[float] = None,
    strict_hint: Optional[bool] = None,
) -> Optional[ExtractedField]:
    text = ""
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        text = ", ".join(cleaned)
    else:
        text = str(value).strip() if value is not None else ""
    if not text:
        return None

    source_list = list(sources) if sources else []
    strict = strict_hint if strict_hint is not None else bool(source_list)
    conf = confidence if confidence is not None else (0.9 if strict else 0.75)

    return ExtractedField(
        name=name,
        value=text,
        strict=strict,
        confidence=conf,
        sources=source_list,
    )


def extract_context(
    item: Dict[str, Any],
    strict_threshold: float,
    lenient_threshold: float,
) -> ExtractedContext:
    fields: List[ExtractedField] = []
    sources: List[str] = []

    context = item.get("context") or {}
    field_list = context.get("fields") if isinstance(context, dict) else None
    if isinstance(field_list, list):
        for raw in field_list:
            if not isinstance(raw, dict):
                continue
            field = _field_from_value(
                name=str(raw.get("field") or raw.get("name") or "note"),
                value=raw.get("value") or raw.get("text") or "",
                sources=_coerce_sources(raw.get("sources") or raw.get("source")),
                confidence=raw.get("confidence"),
                strict_hint=raw.get("strict"),
            )
            if not field:
                continue
            if not field.strict and field.confidence < lenient_threshold:
                continue
            fields.append(field)
            sources.extend(field.sources)

    summary_text = item.get("summary") or item.get("why")
    summary_sources = _coerce_sources(item.get("sources"))
    summary_field = _field_from_value(
        name="summary",
        value=summary_text,
        sources=summary_sources,
        confidence=None,
        strict_hint=None,
    )
    if summary_field:
        if summary_field.strict or summary_field.confidence >= lenient_threshold:
            fields.append(summary_field)
            sources.extend(summary_field.sources)

    if isinstance(context, dict):
        for key in ("moods", "genres", "instrumentation", "comparisons", "era", "themes", "lyrics", "scene"):
            if key not in context:
                continue
            field = _field_from_value(
                name=key,
                value=context.get(key),
                sources=_coerce_sources(context.get("sources") or item.get("sources")),
                confidence=context.get("confidence"),
                strict_hint=None,
            )
            if not field:
                continue
            if not field.strict and field.confidence < lenient_threshold:
                continue
            fields.append(field)
            sources.extend(field.sources)

    metrics = item.get("metrics") if isinstance(item, dict) else None
    if isinstance(metrics, dict):
        for key in ("mood", "genre"):
            if key not in metrics:
                continue
            field = _field_from_value(
                name=key,
                value=metrics.get(key),
                sources=_coerce_sources(item.get("sources")),
                confidence=0.7,
                strict_hint=False,
            )
            if not field:
                continue
            if field.confidence < lenient_threshold:
                continue
            fields.append(field)
            sources.extend(field.sources)

    # De-dupe sources
    seen = set()
    unique_sources: List[str] = []
    for source in sources:
        if source in seen:
            continue
        seen.add(source)
        unique_sources.append(source)

    # If strict fields are empty, allow lenient fields only if any exist.
    strict_fields = [field for field in fields if field.strict]
    if not strict_fields:
        fields = [field for field in fields if field.confidence >= lenient_threshold]

    # If strict coverage is low, keep lenient fields; otherwise, keep strict only.
    if strict_fields:
        strict_ratio = len(strict_fields) / max(1, len(fields))
        if strict_ratio >= strict_threshold:
            fields = strict_fields

    if not fields:
        # Signal missing context for debugging/logging.
        item["_context_missing"] = True

    return ExtractedContext(fields=fields, sources=unique_sources)
