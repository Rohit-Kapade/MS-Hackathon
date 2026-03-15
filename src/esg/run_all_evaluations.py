#!/usr/bin/env -S uv run python
"""Run all three ESG extraction evaluations and generate a combined HTML report.

Executes the raw, document-intelligence, and custom extraction pipelines for
one or more models, then feeds the evaluation results into the HTML report
builder.

Usage::

    uv run python -m esg.run_all_evaluations
    uv run python -m esg.run_all_evaluations --model gpt-5.1 --model gpt-4.1-191849
    uv run python -m esg.run_all_evaluations --skip raw --skip custom
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import cast

from dotenv import dotenv_values

from esg.evaluate import ExtractorFn, run
from esg.report.build_eval_report import build_report

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_METHODS = ("raw", "document_intelligence", "custom")

_AVAILABLE_MODELS: dict[str, str] = {
    "gpt-5.1": "gpt-5_1",
    "gpt-4.1-191849": "gpt-4_1",
    "gpt-4.1-mini-939006": "gpt-4_1-mini",
    "Phi-4": "phi-4",
}
"""Maps deployment names to short slugs used in filenames."""

_DEFAULT_MODELS = list(_AVAILABLE_MODELS.keys())


def _model_slug(model: str) -> str:
    """Return a filesystem-safe short name for a model deployment."""
    return _AVAILABLE_MODELS.get(model, model.replace(".", "-").replace(" ", "_"))


def _build_extractor(
    method: str, endpoint: str
) -> ExtractorFn:
    """Import and wrap the extract function for the given method."""
    if method == "raw":
        from esg.run_extraction_raw import extract

        return cast(ExtractorFn, partial(extract, foundry_project_endpoint=endpoint))

    if method == "document_intelligence":
        from esg.run_extraction_document_intelligence import extract

        return cast(ExtractorFn, partial(extract, foundry_project_endpoint=endpoint))

    if method == "custom":
        from esg.run_extraction_custom import extract

        return cast(ExtractorFn, partial(extract, foundry_project_endpoint=endpoint))

    raise ValueError(f"Unknown extraction method: {method}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run all ESG extraction evaluations and generate a combined report",
    )
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        default=None,
        metavar="DEPLOYMENT",
        help=(
            "Model deployment name to evaluate (can be repeated). "
            f"Available: {', '.join(_AVAILABLE_MODELS)}. "
            "Defaults to all models."
        ),
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=list(_METHODS),
        help="Skip a specific extraction method (can be repeated)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Base output directory (default: output/esg/)",
    )
    args = parser.parse_args()

    config = dotenv_values()
    endpoint = config.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_FOUNDRY_PROJECT_ENDPOINT not set in .env")

    models = args.models or _DEFAULT_MODELS
    methods_to_run = [m for m in _METHODS if m not in args.skip]
    if not methods_to_run:
        raise SystemExit("All methods were skipped — nothing to do.")

    base_output = args.output_dir or (_PROJECT_ROOT / "output" / "esg")

    # Run evaluations for each model × method combination
    # eval_paths[model_slug][method] = Path to eval_results.json
    eval_paths: dict[str, dict[str, Path]] = {}

    for model in models:
        slug = _model_slug(model)
        eval_paths[slug] = {}

        for method in methods_to_run:
            print(
                f"\n{'#' * 70}\n"
                f"# Model: {model}  |  Method: {method}\n"
                f"{'#' * 70}",
                file=sys.stderr,
            )
            extractor = _build_extractor(method, endpoint)
            run_output = run(
                extractor_fn=extractor,
                model=model,
                output_dir=base_output,
                foundry_project_endpoint=endpoint,
            )
            eval_paths[slug][method] = run_output / "eval_results.json"

    # Build one report per model
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    report_paths: list[Path] = []

    for slug, method_results in eval_paths.items():
        report_path = base_output / f"{timestamp}_{slug}_report.html"

        print(
            f"\n{'#' * 70}\n"
            f"# Building report for {slug}\n"
            f"{'#' * 70}",
            file=sys.stderr,
        )

        out = build_report(
            di_path=method_results.get("document_intelligence"),
            raw_path=method_results.get("raw"),
            custom_path=method_results.get("custom"),
            output_path=report_path.resolve(),
        )
        report_paths.append(out)
        print(f"  Report: {out}", file=sys.stderr)

    print(f"\n{'=' * 70}", file=sys.stderr)
    print(f"Generated {len(report_paths)} report(s):", file=sys.stderr)
    for p in report_paths:
        print(f"  {p}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)


if __name__ == "__main__":
    main()
