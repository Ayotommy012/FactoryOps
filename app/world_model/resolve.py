"""Entity and alias resolution matching Meterless specification."""

from __future__ import annotations

from typing import Iterable, Literal
from app.world_model.stable_ids import normalize_name


def levenshtein(a: str, b: str) -> int:
    """Compute classical Levenshtein edit distance between strings a and b."""
    if a == b:
        return 0
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m

    prev = list(range(n + 1))
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, [0] * (n + 1)

    return prev[n]


def name_similarity(raw_a: str, raw_b: str) -> float:
    """Compute string similarity based on normalized Levenshtein distance:

    1 - distance / max(len(a), len(b))
    """
    a = normalize_name(raw_a)
    b = normalize_name(raw_b)
    if not a and not b:
        return 1.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    dist = levenshtein(a, b)
    return 1.0 - (dist / max_len)


ResolutionDecision = Literal["auto-merge", "review", "new"]


def decide(
    similarity: float, auto_merge_at: float = 0.85, review_lower: float = 0.82
) -> ResolutionDecision:
    """Classify similarity according to the reference banded thresholds:

    - similarity >= 0.85: "auto-merge" (0.85 exactly auto-merges)
    - 0.82 <= similarity < 0.85: "review" (0.82 exactly queues for review)
    - similarity < 0.82: "new"
    """
    if similarity >= auto_merge_at:
        return "auto-merge"
    if similarity >= review_lower:
        return "review"
    return "new"


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Compute Jaccard similarity coefficient between two iterables of strings."""
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    intersection_len = len(sa.intersection(sb))
    union_len = len(sa.union(sb))
    if union_len == 0:
        return 0.0
    return intersection_len / union_len
