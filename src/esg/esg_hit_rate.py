"""Hit-rate evaluator for ESG document extraction.

Compatible with ``azure-ai-evaluation``'s ``evaluate()`` API as a custom
callable evaluator.

Recursively walks the expected and actual structures (dicts, lists, and
leaf values) and counts leaf-level matches using string normalisation.

Scoring rules:
- Leaf values compared as stripped strings.
- Missing keys / extra list elements count as misses.
- Both None / both absent → hit.
- Lists of dicts are matched by key fields (not by index).
"""

from __future__ import annotations

from typing import Any


def _find_key_field(items: list) -> str | None:
    """Detect the key field from a list of dicts by finding a string field
    whose values are unique across all items.

    When multiple fields qualify, the one with the longest average value
    is preferred (descriptive identifiers like ``Case`` or ``Description``
    tend to be longer than short codes like ``Severity``).
    """
    dicts = [item for item in items if isinstance(item, dict)]
    if not dicts:
        return None

    # Collect all string-valued fields present in every dict
    common_keys = set(dicts[0].keys())
    for d in dicts[1:]:
        common_keys &= d.keys()

    candidates: list[tuple[str, float]] = []
    for field in common_keys:
        values = [str(d[field]).strip() for d in dicts if isinstance(d.get(field), str)]
        if len(values) == len(dicts) and len(set(values)) == len(values):
            avg_len = sum(len(v) for v in values) / len(values)
            candidates.append((field, avg_len))

    if not candidates:
        return None

    # Pick the field with the longest average value
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _best_match(
    expected_item: dict,
    actual_list: list,
    key_field: str,
    used: set[int],
) -> dict | None:
    """Find the actual item whose *key_field* matches *expected_item*."""
    expected_key = str(expected_item.get(key_field, "")).strip()
    for i, item in enumerate(actual_list):
        if i not in used and isinstance(item, dict) and str(item.get(key_field, "")).strip() == expected_key:
            used.add(i)
            return item
    return None


def _count_leaf_matches(expected: object, actual: object) -> tuple[int, int]:
    """Return ``(hits, total)`` by recursively comparing leaf values."""
    if isinstance(expected, dict):
        hits = total = 0
        actual_dict = actual if isinstance(actual, dict) else {}
        for k, v in expected.items():
            h, t = _count_leaf_matches(v, actual_dict.get(k))
            hits += h
            total += t
        return hits, total

    if isinstance(expected, list):
        hits = total = 0
        actual_list = actual if isinstance(actual, list) else []

        key_field = _find_key_field(expected)
        if key_field is not None:
            # Key-based matching: find each expected item's counterpart
            used: set[int] = set()
            for v in expected:
                if isinstance(v, dict):
                    matched = _best_match(v, actual_list, key_field, used)
                    h, t = _count_leaf_matches(v, matched)
                else:
                    h, t = _count_leaf_matches(v, None)
                hits += h
                total += t
        else:
            # Fallback: positional matching for non-dict lists
            for i, v in enumerate(expected):
                a = actual_list[i] if i < len(actual_list) else None
                h, t = _count_leaf_matches(v, a)
                hits += h
                total += t
        return hits, total

    # Leaf value
    if expected is None and actual is None:
        return 1, 1
    if expected is None or actual is None:
        return 0, 1
    match = str(expected).strip() == str(actual).strip()
    return (1 if match else 0), 1


def esg_hit_rate(
    *, expected: dict[str, Any], actual: dict[str, Any], **kwargs: Any
) -> dict[str, float]:
    """Compute the hit rate between expected and actual ESG extractions.

    Designed to be passed directly to
    ``evaluate(evaluators={"hit_rate": esg_hit_rate})``.

    Returns a dict with ``hit_rate`` (0.0–1.0).
    """
    hits, total = _count_leaf_matches(expected, actual)
    return {"hit_rate": round(hits / total, 4) if total > 0 else 1.0}
