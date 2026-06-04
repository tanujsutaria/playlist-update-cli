"""Backfill sonic features from AcousticBrainz (MBID-keyed precomputed analyses).

Per track: resolve artist/title -> MusicBrainz recording MBID, fetch the
AcousticBrainz high-level + low-level payloads, build the sonic vector
(storage.sonic.build_sonic_vector), and store it in track_sonic. NO audio is
downloaded — only AcousticBrainz's precomputed features (the legally-clean path).

Necessarily SERIAL: MusicBrainz requires ~1 request/second and forbids concurrent
requests, so unlike the deep-search enrichment this cannot be parallelized — it is
throttled instead. Idempotent: the caller passes only tracks lacking sonic data.

PRIVACY: the outbound User-Agent NEVER contains personal info. It defaults to a
generic, non-personal string and is overridable via TUNR_MUSICBRAINZ_UA (set it to
your own contact URL if you like — never an address you don't want in request logs).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from storage.repos import Repositories
from storage.sonic import build_sonic_vector
from storage.vectors import encode_vector

# Generic, non-personal default. Contains NO email/name. Override via env if you
# want to supply your own (non-personal) contact URL.
_DEFAULT_UA = "tunr/1.0"
_MB_SEARCH = "https://musicbrainz.org/ws/2/recording/"
_AB_BASE = "https://acousticbrainz.org/api/v1"
_MB_MIN_SCORE = 85
# Compact subset of high-level labels kept in features_json for transparency.
_HL_KEEP = (
    "danceability",
    "mood_happy",
    "mood_sad",
    "mood_aggressive",
    "mood_relaxed",
    "mood_acoustic",
    "mood_electronic",
    "timbre",
    "voice_instrumental",
)


def user_agent() -> str:
    """Non-personal User-Agent for outbound requests (env-overridable, no PII)."""
    return os.getenv("TUNR_MUSICBRAINZ_UA") or _DEFAULT_UA


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat() + "Z"


def _get_json(url: str, *, accept_404: bool = False) -> Optional[Any]:
    req = urllib.request.Request(
        url, headers={"User-Agent": user_agent(), "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if accept_404 and exc.code == 404:
            return None
        raise


def resolve_mbid(artist: str, name: str, *, min_score: int = _MB_MIN_SCORE) -> Optional[str]:
    """Resolve artist/title to a MusicBrainz recording MBID (top match >= min_score)."""
    query = f'artist:"{artist}" AND recording:"{name}"'
    url = _MB_SEARCH + "?" + urllib.parse.urlencode({"query": query, "fmt": "json", "limit": 3})
    data = _get_json(url) or {}
    recordings = data.get("recordings") or []
    if not recordings:
        return None
    top = recordings[0]
    if int(top.get("score") or 0) < min_score:
        return None
    return top.get("id")


def fetch_features(mbid: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return (high-level classifiers, low-level descriptors) for an MBID, or None
    if AcousticBrainz has no analysis for it."""
    highlevel = _get_json(f"{_AB_BASE}/{mbid}/high-level", accept_404=True)
    if highlevel is None:
        return None
    lowlevel = _get_json(f"{_AB_BASE}/{mbid}/low-level", accept_404=True) or {}
    return highlevel.get("highlevel") or {}, lowlevel


def _selected_features(highlevel: Dict[str, Any], lowlevel: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in _HL_KEEP:
        classifier = highlevel.get(key)
        if isinstance(classifier, dict):
            out[key] = {"value": classifier.get("value"), "p": classifier.get("probability")}
    rhythm = lowlevel.get("rhythm") or {}
    tonal = lowlevel.get("tonal") or {}
    out["bpm"] = rhythm.get("bpm")
    out["key"] = f"{tonal.get('key_key')} {tonal.get('key_scale')}".strip()
    return out


def backfill_sonic(
    repos: Repositories,
    tracks: List[Tuple[str, str, str]],
    *,
    on_result: Optional[Callable[[str, str, str], None]] = None,
    throttle: float = 1.1,
) -> Dict[str, int]:
    """Resolve + store sonic features for each (track_id, name, artist).

    Serial, throttled to respect MusicBrainz's ~1 req/sec. Each track ends in one
    of: stored / no_mbid / no_data / failed. Writes are committed per track so an
    interrupted run is resumable. Returns those counts.
    """
    counts = {"stored": 0, "no_mbid": 0, "no_data": 0, "failed": 0}

    def _report(status: str, name: str, artist: str) -> None:
        counts[status] += 1
        if on_result is not None:
            on_result(status, name, artist)

    now = _now()
    for index, (track_id, name, artist) in enumerate(tracks):
        if index and throttle:
            time.sleep(throttle)  # MusicBrainz ~1 req/sec; do NOT parallelize
        try:
            mbid = resolve_mbid(artist, name)
        except Exception:
            _report("failed", name, artist)
            continue
        if not mbid:
            _report("no_mbid", name, artist)
            continue
        try:
            features = fetch_features(mbid)
        except Exception:
            _report("failed", name, artist)
            continue
        if features is None:
            _report("no_data", name, artist)
            continue
        highlevel, lowlevel = features
        vector = build_sonic_vector(highlevel, lowlevel)
        repos.sonic.upsert(
            {
                "track_id": track_id,
                "mbid": mbid,
                "sonic_blob": encode_vector(vector),
                "sonic_dim": len(vector),
                "features_json": json.dumps(_selected_features(highlevel, lowlevel)),
                "source": "acousticbrainz",
                "created_at": now,
            }
        )
        repos.conn.commit()
        _report("stored", name, artist)
    return counts
