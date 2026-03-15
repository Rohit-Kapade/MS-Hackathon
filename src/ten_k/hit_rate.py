"""Hit-rate evaluator for 10-K financial data extraction.

Compatible with ``azure-ai-evaluation``'s ``evaluate()`` API as a custom
callable evaluator.

Scoring rules:
- null/null  → hit  (both absent)
- null/value → miss
- value/null → miss
- Otherwise  → hit if within 0.1 % relative tolerance
"""

from __future__ import annotations

import math
from typing import Any

RELATIVE_TOLERANCE = 0.001  # 0.1 %


def _values_match(expected: float | None, actual: float | None) -> bool:
    """Compare two optional floats with a relative tolerance of 0.1 %.

    Also accepts a match when the values differ by a factor of 1000
    (e.g. thousands vs millions) to handle documents that report in
    different units than the ground truth.
    """
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    if math.isclose(expected, actual, rel_tol=RELATIVE_TOLERANCE):
        return True
    if expected != 0 and math.isclose(expected * 1000, actual, rel_tol=RELATIVE_TOLERANCE):
        return True
    if actual != 0 and math.isclose(expected, actual * 1000, rel_tol=RELATIVE_TOLERANCE):
        return True
    return False


def hit_rate(*, expected: dict[str, Any], actual: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Compute the hit rate between expected and actual flat extractions.

    Designed to be passed directly to ``evaluate(evaluators={"hit_rate": hit_rate})``.

    Returns a dict with ``hit_rate`` (0.0–1.0) and ``field_matches``
    mapping each field name to ``True``/``False``.
    """
    hits = 0
    total = 0
    field_matches: dict[str, bool] = {}

    for key in expected:
        total += 1
        match = _values_match(expected[key], actual.get(key))
        field_matches[key] = match
        if match:
            hits += 1

    score = hits / total if total > 0 else 1.0
    return {"hit_rate": score, "field_matches": field_matches}
