"""Build a fixed-dimension, 0–1 sonic feature vector from AcousticBrainz data.

AcousticBrainz gives two payloads per recording (MBID):
  * high-level — binary classifiers, each ``{"value": <label>, "probability": p}``
    (danceability, the mood_* set, timbre, tonal/atonal, voice/instrumental, …).
  * low-level  — raw acoustic descriptors (bpm, key strength, loudness, dissonance).

We fold each binary classifier to the probability of its POSITIVE label (so
``mood_happy={"value":"not_happy","probability":0.67}`` becomes happiness 0.33),
and normalize a few low-level scalars into 0–1. The output order is FIXED — the
stored blob's meaning depends on ``SONIC_FEATURES`` staying append-only.

This is the genuinely-acoustic signal the semantic (text) embedding can't capture:
tempo, loudness, dissonance, key strength, plus the mood/danceability/timbre feel.
No audio is fetched or stored — only AcousticBrainz's precomputed features.
"""

from __future__ import annotations

from typing import Any, Dict, List

# (high-level classifier name, label counted as the 1.0 end of the axis).
_HL_POSITIVE = [
    ("danceability", "danceable"),
    ("mood_acoustic", "acoustic"),
    ("mood_aggressive", "aggressive"),
    ("mood_electronic", "electronic"),
    ("mood_happy", "happy"),
    ("mood_party", "party"),
    ("mood_relaxed", "relaxed"),
    ("mood_sad", "sad"),
    ("timbre", "bright"),
    ("tonal_atonal", "tonal"),
    ("voice_instrumental", "voice"),
]

# Canonical, append-only feature names (high-level folded probs + low-level scalars).
SONIC_FEATURES: List[str] = [name for name, _ in _HL_POSITIVE] + [
    "bpm_norm",
    "average_loudness",
    "dissonance",
    "key_strength",
]
SONIC_DIM = len(SONIC_FEATURES)

# Tempo normalization window (BPM): 40–220 maps to 0–1.
_BPM_LO, _BPM_HI = 40.0, 220.0


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _scalar(value: Any) -> float:
    """AcousticBrainz low-level fields are sometimes a bare number, sometimes a
    stats dict ``{"mean": ...}``. Coerce either to a float (0.0 if absent)."""
    if isinstance(value, dict):
        value = value.get("mean")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _positive_prob(classifier: Any, positive_label: str) -> float:
    if not isinstance(classifier, dict):
        return 0.0
    prob = _scalar(classifier.get("probability"))
    return prob if classifier.get("value") == positive_label else 1.0 - prob


def build_sonic_vector(highlevel: Dict[str, Any], lowlevel: Dict[str, Any]) -> List[float]:
    """Return a ``SONIC_DIM``-length 0–1 vector from AB high-level + low-level dicts.

    Missing fields degrade to 0.0 rather than raising, so a partial payload still
    yields a usable (if sparser) vector.
    """
    highlevel = highlevel or {}
    lowlevel = lowlevel or {}
    vector = [_positive_prob(highlevel.get(name), label) for name, label in _HL_POSITIVE]

    rhythm = lowlevel.get("rhythm") or {}
    low = lowlevel.get("lowlevel") or {}
    tonal = lowlevel.get("tonal") or {}

    bpm = _scalar(rhythm.get("bpm"))
    vector.append(_clamp01((bpm - _BPM_LO) / (_BPM_HI - _BPM_LO)) if bpm else 0.0)
    vector.append(_clamp01(_scalar(low.get("average_loudness"))))
    vector.append(_clamp01(_scalar(low.get("dissonance"))))
    vector.append(_clamp01(_scalar(tonal.get("key_strength"))))
    return vector
