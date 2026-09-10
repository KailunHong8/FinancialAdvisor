"""Description similarity on normalized text (§11.1). rapidfuzz for C-speed."""
from __future__ import annotations

from rapidfuzz import fuzz

from ..models import normalize_description


def similarity(a: str, b: str) -> float:
    """0.0..1.0 token-sort ratio over normalized descriptions."""
    na, nb = normalize_description(a), normalize_description(b)
    if not na or not nb:
        return 0.0
    return fuzz.token_sort_ratio(na, nb) / 100.0
