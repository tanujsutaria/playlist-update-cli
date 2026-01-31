from __future__ import annotations

from typing import Any, Dict, List, Tuple


def normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def canonical_track_id(artist: str, song: str) -> str:
    return f"{normalize_text(artist).lower()}|||{normalize_text(song).lower()}"


def canonicalize_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    for item in results:
        song = normalize_text(str(item.get("song") or item.get("name") or ""))
        artist = normalize_text(str(item.get("artist") or ""))
        if not song or not artist:
            continue
        track_id = canonical_track_id(artist, song)
        item["song"] = song
        item["artist"] = artist
        item["track_id"] = track_id

        existing = deduped.get(track_id)
        if not existing:
            deduped[track_id] = item
            continue

        existing_sources = existing.get("sources") or []
        candidate_sources = item.get("sources") or []
        if len(candidate_sources) > len(existing_sources):
            deduped[track_id] = item

    return list(deduped.values())
