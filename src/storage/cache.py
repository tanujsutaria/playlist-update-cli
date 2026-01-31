from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional


def normalize_query(text: str) -> str:
    return " ".join(text.strip().lower().split())


def normalize_constraints(constraints: Optional[Dict[str, Any]]) -> str:
    if not constraints:
        return ""
    return json.dumps(constraints, sort_keys=True, separators=(",", ":"))


def compute_query_hash(text: str, constraints: Optional[Dict[str, Any]] = None) -> str:
    normalized = normalize_query(text)
    constraints_blob = normalize_constraints(constraints)
    payload = f"{normalized}|{constraints_blob}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
