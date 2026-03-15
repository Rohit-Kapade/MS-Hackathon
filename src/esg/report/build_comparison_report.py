"""Generate a comparison HTML report across models, extraction methods, and scenarios.

Scans the output/esg/ directory tree for eval_results.json files and builds
a single interactive HTML report comparing hit rates across:
  - Scenarios (B1 = Risk Assessment, B7 = Description & Practice, C100 = Controversies)
  - Extraction methods (raw, document_intelligence, custom)
  - Models (gpt-5.1, gpt-4.1, gpt-4.1-mini, phi-4)

Usage::

    uv run python -m src.esg.report.build_comparison_report
    uv run python -m src.esg.report.build_comparison_report -o output/esg/comparison.html
    uv run python -m src.esg.report.build_comparison_report --input-dir output/esg

The script auto-discovers eval_results.json files whose parent directory name
contains a model slug (e.g. ``260311_185033_gpt-5-1``).  Only the **latest**
run per model × method combination is kept.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

_REPORT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Maps directory model slug fragments → display names
_MODEL_SLUGS: dict[str, str] = {
    "gpt-5-1": "GPT-5.1",
    "gpt-5_1": "GPT-5.1",
    "gpt-4-1-191849": "GPT-4.1",
    "gpt-4_1": "GPT-4.1",
    "gpt-4-1-mini-939006": "GPT-4.1 Mini",
    "gpt-4_1-mini": "GPT-4.1 Mini",
    "phi-4": "Phi-4",
    "Phi-4": "Phi-4",
}

_METHOD_DISPLAY: dict[str, str] = {
    "raw": "Raw",
    "document_intelligence": "Document Intelligence",
    "custom": "Custom",
}

_SCENARIO_DISPLAY: dict[str, str] = {
    "B1": "Scenario 1 - Risk Assessment",
    "B7": "Scenario 2 - Description and Practice",
    "C100": "Scenario 3 - Controversies",
}


def _detect_model(dir_name: str) -> str | None:
    """Extract model display name from a run directory name."""
    for slug, display in _MODEL_SLUGS.items():
        if slug in dir_name:
            return display
    return None


def _detect_method(path: Path) -> str | None:
    """Walk up from eval_results.json to find which method directory it's under."""
    for parent in path.parents:
        if parent.name in _METHOD_DISPLAY:
            return parent.name
        # Stop at the esg/ level
        if parent.name == "esg":
            break
    return None


def _extract_timestamp(dir_name: str) -> str:
    """Extract timestamp prefix from directory name like '260311_185033_gpt-5-1'."""
    match = re.match(r"^(\d{6}_\d{6})", dir_name)
    return match.group(1) if match else ""


def discover_results(base_dir: Path) -> dict[str, dict[str, dict]]:
    """Scan base_dir for eval_results.json files and return structured data.

    Returns a nested dict: {model_name: {method_name: eval_data}}.
    Only keeps the latest run per model × method.
    """
    candidates: dict[str, dict[str, tuple[str, dict]]] = {}

    for eval_path in sorted(base_dir.rglob("eval_results.json")):
        model = _detect_model(eval_path.parent.name)
        method = _detect_method(eval_path)
        if not model or not method:
            continue

        timestamp = _extract_timestamp(eval_path.parent.name)

        with open(eval_path, encoding="utf-8") as f:
            data = json.load(f)

        data["_run"] = eval_path.parent.name
        data["_method"] = method

        if model not in candidates:
            candidates[model] = {}
        existing = candidates[model].get(method)
        if not existing or timestamp > existing[0]:
            candidates[model][method] = (timestamp, data)

    return {
        model: {method: val[1] for method, val in methods.items()}
        for model, methods in candidates.items()
    }


def _replace_placeholder(html: str, name: str, value: str) -> str:
    pattern = r"\{\{\s*" + re.escape(name) + r"\s*\}\}"
    return re.sub(pattern, lambda _m: value, html)


def build_comparison_report(
    *,
    results: dict[str, dict[str, dict]],
    output_path: Path,
) -> Path:
    """Build the comparison HTML report and write it to output_path."""
    # Build a compact payload for the template
    payload = {
        "models": {},
        "methods": sorted(
            {m for model_data in results.values() for m in model_data},
            key=lambda x: list(_METHOD_DISPLAY.keys()).index(x)
            if x in _METHOD_DISPLAY
            else 99,
        ),
        "method_display": _METHOD_DISPLAY,
        "scenario_display": _SCENARIO_DISPLAY,
    }

    for model, methods in results.items():
        payload["models"][model] = {}
        for method, data in methods.items():
            rows = data.get("rows", [])
            metrics = data.get("metrics", {})
            payload["models"][model][method] = {
                "overall": metrics.get("hit_rate.hit_rate", 0),
                "scenarios": {
                    row["inputs.scenario"]: row["outputs.hit_rate.hit_rate"]
                    for row in rows
                },
                "run": data.get("_run", ""),
            }

    template = (_REPORT_DIR / "comparison_template.html").read_text(encoding="utf-8")
    css = (_REPORT_DIR / "comparison_style.css").read_text(encoding="utf-8")

    html = template
    html = _replace_placeholder(html, "CSS", css)
    html = _replace_placeholder(html, "DATA_JSON", json.dumps(payload, ensure_ascii=False))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a comparison HTML report across models, methods, and scenarios",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Directory to scan for eval_results.json (default: output/esg/)",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output HTML file path",
    )
    args = parser.parse_args()

    base_dir = args.input_dir or (_PROJECT_ROOT / "output" / "esg")
    if not base_dir.exists():
        raise SystemExit(f"Input directory not found: {base_dir}")

    results = discover_results(base_dir)
    if not results:
        raise SystemExit(
            f"No eval_results.json files found with recognized model names in {base_dir}"
        )

    if args.output is None:
        timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
        args.output = base_dir / f"{timestamp}_comparison_report.html"

    out = build_comparison_report(results=results, output_path=args.output.resolve())
    print(f"Comparison report written to {out}")
    print(f"Models found: {', '.join(sorted(results.keys()))}")
    for model, methods in sorted(results.items()):
        for method in sorted(methods.keys()):
            print(f"  {model} / {_METHOD_DISPLAY.get(method, method)}")


if __name__ == "__main__":
    main()
