"""Evaluation and comparison utilities for structured extraction results.

Provides field-level comparison between extracted output and ground truth
for both dict (key-value) and list (table-row) formats.
"""

import sys


def _leaf_match(extracted_val: object, expected_val: object) -> bool:
    """Compare two leaf values with string normalisation.

    Handles:
    - int/string type differences (``4`` == ``"4"``)
    - leading/trailing whitespace
    - single-character visual lookalikes: letter ``'O'`` == digit ``'0'``
    """
    e_str = str(expected_val).strip()
    a_str = str(extracted_val).strip()
    if a_str == e_str:
        return True
    # Single-char normalisation: uppercase and treat letter O as digit 0
    if len(e_str) == 1 and len(a_str) == 1:
        def _norm(c: str) -> str:
            return c.upper().replace("O", "0")
        return _norm(a_str) == _norm(e_str)
    return False


def compare_with_ground_truth(extracted: object, ground_truth: object) -> dict:
    """Compare extracted output with ground truth.

    Handles both dict (key-value) and list (table row) ground truth formats.
    """
    if isinstance(ground_truth, list):
        return _compare_list(
            extracted if isinstance(extracted, list) else [],
            ground_truth,
        )
    if isinstance(ground_truth, dict):
        return _compare_dict(
            extracted if isinstance(extracted, dict) else {},
            ground_truth,
        )
    raise ValueError(f"Unsupported ground truth type: {type(ground_truth)}")


def _compare_dict(extracted: dict, ground_truth: dict) -> dict:
    """Compare dict-structured ground truth (nested or flat, including list values)."""
    results: dict = {}
    total = 0
    matches = 0

    for field, expected in ground_truth.items():
        actual = extracted.get(field)

        if isinstance(expected, dict):
            actual_dict = actual if isinstance(actual, dict) else {}
            field_results: dict = {}
            for sub_key, expected_val in expected.items():
                total += 1
                actual_val = actual_dict.get(sub_key)
                is_match = _leaf_match(actual_val, expected_val)
                if is_match:
                    matches += 1
                field_results[sub_key] = {
                    "expected": expected_val,
                    "extracted": actual_val,
                    "match": is_match,
                }
            results[field] = field_results

        elif isinstance(expected, list):
            # Recurse into list-valued fields (e.g. ENVIRONMENT/SOCIAL sections)
            actual_list = actual if isinstance(actual, list) else []
            sub = _compare_list(actual_list, expected)
            sub_total = sub["summary"]["total_fields"]
            sub_matches = sub["summary"]["matches"]
            total += sub_total
            matches += sub_matches
            results[field] = {
                "expected": expected,
                "extracted": actual,
                "match": sub_matches == sub_total and sub_total > 0,
            }

        else:
            total += 1
            is_match = _leaf_match(actual, expected)
            if is_match:
                matches += 1
            results[field] = {
                "expected": expected,
                "extracted": actual,
                "match": is_match,
            }

    accuracy = (matches / total * 100) if total > 0 else 0.0
    return {
        "field_results": results,
        "summary": {
            "total_fields": total,
            "matches": matches,
            "mismatches": total - matches,
            "accuracy_percent": round(accuracy, 2),
        },
    }


def _compare_list(extracted: list, ground_truth: list) -> dict:
    """Compare list-structured ground truth, recursing into nested dicts/lists."""
    total = 0
    matches = 0
    row_results: list[dict] = []

    for i, expected_row in enumerate(ground_truth):
        actual_row = extracted[i] if i < len(extracted) else {}
        if not isinstance(actual_row, dict):
            actual_row = {}
        row_result: dict = {}
        for key, expected_val in expected_row.items():
            # Handle the 'Practice Score' / 'Practices Score' key-name variant
            actual_val = actual_row.get(key)
            if actual_val is None:
                alt_key = (
                    "Practices Score" if key == "Practice Score"
                    else "Practice Score" if key == "Practices Score"
                    else None
                )
                if alt_key is not None:
                    actual_val = actual_row.get(alt_key)

            if isinstance(expected_val, dict):
                actual_dict = actual_val if isinstance(actual_val, dict) else {}
                sub = _compare_dict(actual_dict, expected_val)
                sub_t = sub["summary"]["total_fields"]
                sub_m = sub["summary"]["matches"]
                total += sub_t
                matches += sub_m
                row_result[key] = {
                    "expected": expected_val,
                    "extracted": actual_val,
                    "match": sub_m == sub_t and sub_t > 0,
                }
            elif isinstance(expected_val, list):
                actual_list = actual_val if isinstance(actual_val, list) else []
                sub = _compare_list(actual_list, expected_val)
                sub_t = sub["summary"]["total_fields"]
                sub_m = sub["summary"]["matches"]
                total += sub_t
                matches += sub_m
                row_result[key] = {
                    "expected": expected_val,
                    "extracted": actual_val,
                    "match": sub_m == sub_t and sub_t > 0,
                }
            else:
                total += 1
                is_match = _leaf_match(actual_val, expected_val)
                if is_match:
                    matches += 1
                row_result[key] = {
                    "expected": expected_val,
                    "extracted": actual_val,
                    "match": is_match,
                }
        row_results.append(row_result)

    extra_rows = max(0, len(extracted) - len(ground_truth))
    accuracy = (matches / total * 100) if total > 0 else 0.0
    return {
        "row_results": row_results,
        "summary": {
            "total_fields": total,
            "matches": matches,
            "mismatches": total - matches,
            "extra_extracted_rows": extra_rows,
            "accuracy_percent": round(accuracy, 2),
        },
    }


def print_evaluation(evaluation: dict) -> None:
    """Print a human-readable evaluation report to stderr."""
    summary = evaluation["summary"]
    print(
        f"  Accuracy: {summary['accuracy_percent']}% "
        f"({summary['matches']}/{summary['total_fields']} fields)",
        file=sys.stderr,
    )

    # Dict results
    if "field_results" in evaluation:
        for field, details in evaluation["field_results"].items():
            if isinstance(details, dict) and "match" in details:
                status = "✓" if details["match"] else "✗"
                print(
                    f"  {status} {field}: expected={details['expected']}, "
                    f"extracted={details['extracted']}",
                    file=sys.stderr,
                )
            elif isinstance(details, dict):
                for sub_key, sub_details in details.items():
                    status = "✓" if sub_details["match"] else "✗"
                    print(
                        f"  {status} {field}.{sub_key}: "
                        f"expected={sub_details['expected']}, "
                        f"extracted={sub_details['extracted']}",
                        file=sys.stderr,
                    )

    # List (table row) results
    if "row_results" in evaluation:
        for i, row in enumerate(evaluation["row_results"]):
            for key, details in row.items():
                status = "✓" if details["match"] else "✗"
                print(
                    f"  {status} row[{i}].{key}: "
                    f"expected={details['expected']}, "
                    f"extracted={details['extracted']}",
                    file=sys.stderr,
                )
