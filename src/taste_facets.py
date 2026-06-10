"""Pure aggregation helpers over `track_context.fields_json` (the taste facets).

All parsing/folding/counting of the enriched per-track field entries lives here
exactly once, so /taste, /stats, and /profile agree on every number. No DB and
no Rich imports — callers pass `(track_id, fields_json)` row iterables, which
keeps this module offline-testable and mypy-clean.

Data-quality gotchas this module neutralizes (verified on the live corpus):
  * tracks are tagged under BOTH `mood`/`moods` and `genre`/`genres` raw field
    names — counted per canonical field, never twice;
  * values are comma-joined strings, not arrays — split before counting;
  * spelling variants (`synthpop` / `synth-pop` / `synth pop`) are folded for
    counting while the most frequent surface form stays the display label;
  * `summary` entries leak `claude:`-prefixed judge rationales — filtered;
  * era values mix decade tokens, years, and free text — unparseable values
    are counted (and captioned) as unbucketable, never guessed.
"""

from __future__ import annotations

import json
import re
import string
from collections import Counter
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

# Canonical facet name -> raw `field` names that feed it. Live data tags 679
# tracks under both "mood" and "moods" (1186 under both genre spellings) —
# union the sources per canonical field or every count doubles.
CANONICAL_FIELDS: Dict[str, FrozenSet[str]] = {
    "moods": frozenset({"mood", "moods"}),
    "genres": frozenset({"genre", "genres"}),
    "era": frozenset({"era"}),
    "themes": frozenset({"themes"}),
    "instrumentation": frozenset({"instrumentation"}),
    "comparisons": frozenset({"comparisons"}),
}

# Raw field name -> canonical facet (inverse of CANONICAL_FIELDS).
_RAW_TO_CANONICAL: Dict[str, str] = {
    raw: canonical for canonical, raws in CANONICAL_FIELDS.items() for raw in raws
}


def split_values(raw: str) -> List[str]:
    """Comma-split, strip, lowercase, drop empties.

    Stored values are comma-joined strings, not arrays (1412/1420 live mood
    entries contain commas).
    """
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def parse_fields(fields_json: str) -> Dict[str, List[str]]:
    """Parse a `fields_json` blob into {canonical facet: [values]}.

    Per canonical field, unions all its raw source names, splits each value
    string, and dedupes per track PRESERVING first-seen order (the LLM lists
    salient values first — per-track chips depend on order). Malformed JSON
    returns {} (never raises). `summary` is excluded entirely here; see
    `clean_summary`.
    """
    try:
        entries = json.loads(fields_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(entries, list):
        return {}
    out: Dict[str, List[str]] = {}
    seen: Dict[str, Set[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        field = entry.get("field")
        value = entry.get("value")
        if not isinstance(field, str) or not isinstance(value, str):
            continue
        canonical = _RAW_TO_CANONICAL.get(field.strip().lower())
        if canonical is None:
            continue
        bucket = out.setdefault(canonical, [])
        seen_set = seen.setdefault(canonical, set())
        for item in split_values(value):
            if item not in seen_set:
                seen_set.add(item)
                bucket.append(item)
    return out


# Explicit slash-form duplicates only — everything else folds via the regex.
GENRE_TABLE: Dict[str, str] = {"alternative/indie rock": "indie rock"}


def fold_genre(value: str) -> str:
    """Fold KEY for genre counting: GENRE_TABLE lookup, then strip hyphens and
    whitespace. Merges synthpop/synth-pop/synth pop, indie-rock/indie rock,
    art-pop/art pop. Display uses the most frequent surface form (see
    `facet_track_counts`)."""
    value = GENRE_TABLE.get(value, value)
    return re.sub(r"[-\s]", "", value)


INSTRUMENT_FOLDS: Dict[str, str] = {
    "guitars": "guitar",
    "synths": "synth",
    "synthesizer": "synth",
    "synthesizers": "synth",
    "keys": "keyboards",
    "keyboard": "keyboards",
    "bass guitar": "bass",
    "vocal": "vocals",
    "drum": "drums",
}  # acoustic guitar / electric guitar stay distinct — texture is signal


def fold_instrument(value: str) -> str:
    """Fold instrumentation synonyms/plurals to one counting key."""
    return INSTRUMENT_FOLDS.get(value, value)


def facet_track_counts(
    rows: Iterable[Tuple[str, str]],  # (track_id, fields_json)
    canonical: str,
    track_ids: Optional[Set[str]] = None,  # scope filter (e.g. the seed)
    fold: Optional[Callable[[str], str]] = None,
) -> List[Tuple[str, int]]:
    """Count TRACKS TAGGED with each (folded) value of a canonical facet.

    Per-track dedupe happens AFTER folding, so a track tagged
    'synthpop, synth-pop' counts once. The display label is the most frequent
    surface form of the fold key. Sorted count desc, then label asc
    (deterministic ties).
    """
    track_counts: Counter[str] = Counter()
    surface_counts: Dict[str, Counter[str]] = {}
    for track_id, fields_json in rows:
        if track_ids is not None and track_id not in track_ids:
            continue
        values = parse_fields(fields_json).get(canonical, [])
        folded_seen: Set[str] = set()
        for value in values:
            key = fold(value) if fold is not None else value
            surface_counts.setdefault(key, Counter())[value] += 1
            if key not in folded_seen:
                folded_seen.add(key)
                track_counts[key] += 1
    labeled: List[Tuple[str, int]] = []
    for key, count in track_counts.items():
        # Most frequent surface form; ties break alphabetically (deterministic).
        label = min(surface_counts[key].items(), key=lambda kv: (-kv[1], kv[0]))[0]
        labeled.append((label, count))
    return sorted(labeled, key=lambda kv: (-kv[1], kv[0]))


_DEC = re.compile(r"\b((?:19|20)\d)0s\b")
_YR_TOKEN = re.compile(r"\b(\d{4})\b")


def _decade_of_value(value: str) -> Optional[str]:
    """A decade token wins; else 4-digit years, bounded to 19xx/20xx.

    ALL year tokens in the value must be in-bound and agree on one decade —
    '2017-2018' parses to 2010s, but 'set in 1860-1910 (conceptual)' (an
    out-of-bound year) and '1995-2005' (a cross-decade range) never parse:
    unbucketable beats guessed.
    """
    match = _DEC.search(value)
    if match:
        return f"{match.group(1)}0s"
    years = _YR_TOKEN.findall(value)
    if not years:
        return None
    decades = set()
    for year in years:
        if year[:2] not in ("19", "20"):
            return None
        decades.add(f"{int(year) // 10 * 10}s")
    if len(decades) == 1:
        return decades.pop()
    return None


def decade_of(era_values: Iterable[str]) -> Optional[str]:
    """Resolve a track's era values to one decade label, or None.

    Per value: a decade token ('2010s', 'late 2010s', '2010s indie') wins; else
    a 4-digit year maps to its decade. The track decade is the MODAL decade
    across its values; ties go to the most recent. None when nothing parses
    ('post-punk revival', 'reagan-era') — never guessed.
    """
    decades = Counter(
        decade for decade in (_decade_of_value(v) for v in era_values) if decade is not None
    )
    if not decades:
        return None
    # Modal decade; tie -> most recent (decade labels sort numerically by prefix).
    return max(decades.items(), key=lambda kv: (kv[1], int(kv[0][:-1])))[0]


def decade_histogram(
    rows: Iterable[Tuple[str, str]],
    track_ids: Optional[Set[str]] = None,
) -> Tuple[List[Tuple[str, int]], int, int]:
    """([(decade, n_tracks)] count desc (ties: most recent first), n_datable,
    n_unbucketable). `unbucketable` counts tracks WITH era values that defied
    parsing — named in captions, never guessed. Tracks without era values are
    not counted at all."""
    buckets: Counter[str] = Counter()
    datable = 0
    unbucketable = 0
    for track_id, fields_json in rows:
        if track_ids is not None and track_id not in track_ids:
            continue
        era_values = parse_fields(fields_json).get("era", [])
        if not era_values:
            continue
        decade = decade_of(era_values)
        if decade is None:
            unbucketable += 1
        else:
            datable += 1
            buckets[decade] += 1
    ordered = sorted(buckets.items(), key=lambda kv: (-kv[1], -int(kv[0][:-1])))
    return ordered, datable, unbucketable


def clean_summary(fields: List[Dict[str, object]]) -> Optional[str]:
    """First summary entry NOT starting with 'claude:' (1230 judge-rationale
    strings leak there — MANDATORY filter before any summary display, ever)."""
    for entry in fields:
        if not isinstance(entry, dict):
            continue
        field = entry.get("field")
        value = entry.get("value")
        if not isinstance(field, str) or field.strip().lower() != "summary":
            continue
        if not isinstance(value, str):
            continue
        if value.strip().lower().startswith("claude:"):
            continue
        return value
    return None


def taste_title(
    mood_counts: Sequence[Tuple[str, int]],
    genre_counts: Sequence[Tuple[str, int]],
) -> Optional[str]:
    """Deterministic headline, no LLM. Needs top mood >= 5 tracks AND top genre
    >= 5 tracks, else None. A second genre at >= 35% of the first earns an
    'undertow' clause. Returns lowercase; display via `headline_case`."""
    if not mood_counts or not genre_counts:
        return None
    mood, mood_n = mood_counts[0]
    genre, genre_n = genre_counts[0]
    if mood_n < 5 or genre_n < 5:
        return None
    base = f"{mood} {genre}"
    if len(genre_counts) > 1 and genre_counts[1][1] >= 0.35 * genre_n:
        undertow = genre_counts[1][0]
        article = "an" if undertow[:1].lower() in "aeiou" else "a"
        base += f", with {article} {undertow} undertow"
    return base


_SMALL = frozenset({"a", "an", "the", "with", "of", "in", "and", "for", "on"})


def _cap_word(word: str) -> str:
    return "-".join(part[:1].upper() + part[1:] for part in word.split("-"))


def headline_case(s: str) -> str:
    """Title-case for the masthead: capitalize each word (hyphen parts too)
    except small words when not first. NOT all-caps. e.g.
    'introspective indie rock, with a post-punk undertow' ->
    'Introspective Indie Rock, with a Post-Punk Undertow'."""
    words = s.split(" ")
    out: List[str] = []
    for index, word in enumerate(words):
        core = word.strip(string.punctuation).lower()
        if index > 0 and core in _SMALL:
            out.append(word)
        else:
            out.append(_cap_word(word))
    return " ".join(out)


def display_name(name: str) -> str:
    """Render-time casing for the ~97% lowercased migration names.

    `string.capwords` (not `str.title`) so "let's eat grandma" becomes
    "Let's Eat Grandma", not "Let'S...". Already-cased names ('Fontaines D.C.',
    'DIIV') pass through untouched. Stored values stay lowercase — this is
    display only (track_id integrity)."""
    return string.capwords(name) if name == name.lower() else name
