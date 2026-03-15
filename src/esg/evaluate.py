"""
Evaluation loop for ESG document extraction.

1. Auto-discovers ground-truth scenarios from ESG-files/ground_truth/Scenario_*/.
2. Runs a pluggable extraction function on each scenario's PDF + prompt.
3. Computes a leaf-level hit-rate via the azure-ai-evaluation SDK.
4. Optionally logs results to the Foundry project for portal visibility.

Usage:
    uv run python -m esg.evaluate
    uv run python -m esg.evaluate --model gpt-4.1-191849
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from functools import partial
from pathlib import Path
from pprint import pprint
from typing import Any, Protocol

from azure.ai.evaluation import EvaluationResult, evaluate
from pydantic import BaseModel

from esg.esg_hit_rate import _count_leaf_matches, esg_hit_rate


# ---------------------------------------------------------------------------
# Extractor protocol — any callable matching this signature can be plugged in
# ---------------------------------------------------------------------------


class ExtractorFn(Protocol):
    """Interface implemented by all ESG extraction approaches."""

    def __call__(
        self,
        pdf_path: Path,
        prompt: str,
        model: str,
        schema_name: str | None = None,
    ) -> dict[str, Any]: ...


_MODULE_TO_METHOD: dict[str, str] = {
    "esg.run_extraction_raw": "raw",
    "esg.run_extraction_document_intelligence": "document_intelligence",
    "esg.run_extraction_custom": "custom",
}


def _get_extraction_method(extractor_fn: ExtractorFn) -> str:
    """Identify the extraction method from an extractor callable.

    Inspects the module of *extractor_fn* (unwrapping ``functools.partial``
    if necessary) and returns ``"raw"``, ``"document_intelligence"``, or
    ``"custom"``.
    """
    fn = extractor_fn
    if isinstance(fn, partial):
        fn = fn.func  # type: ignore[assignment]
    module = getattr(fn, "__module__", "") or ""
    for mod_prefix, method in _MODULE_TO_METHOD.items():
        if module.startswith(mod_prefix):
            return method
    raise ValueError(
        f"Cannot determine extraction method from {extractor_fn!r} "
        f"(module={module!r})"
    )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class EvaluationEntry(BaseModel):
    """One ground-truth scenario loaded from an ESG ground-truth JSON file."""

    id: str
    query: str
    prompt: str
    file_name: str
    ground_truth: dict[str, Any] | list[Any]
    schema_name: str | None = None
    context: str | None = None
    page: str | None = None


class InferenceResult(BaseModel):
    """Pairs an evaluation entry with its extracted response."""

    entry: EvaluationEntry
    response: dict[str, Any] | list[Any]


# ---------------------------------------------------------------------------
# Dataset — auto-discovered from ESG-files/ground_truth/Scenario_*/
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_GROUND_TRUTH_DIR = _PROJECT_ROOT / "ESG-files" / "ground_truth"
_DATA_DIR = _PROJECT_ROOT / "ESG-files" / "data"


def _load_dataset(
    ground_truth_dir: Path | None = None,
) -> list[EvaluationEntry]:
    """Load all ground-truth scenarios by walking Scenario_*/ sub-dirs."""
    gt_dir = ground_truth_dir or _GROUND_TRUTH_DIR
    entries: list[EvaluationEntry] = []
    for scenario_dir in sorted(gt_dir.iterdir()):
        if not scenario_dir.is_dir() or not scenario_dir.name.startswith("Scenario_"):
            continue
        for json_file in sorted(scenario_dir.glob("*.json")):
            data = json.loads(json_file.read_text())
            entries.append(EvaluationEntry.model_validate(data))
    if not entries:
        raise FileNotFoundError(
            f"No ground-truth JSON files found under {gt_dir}/Scenario_*/"
        )
    return entries


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def _run_inference(
    dataset: list[EvaluationEntry],
    extractor_fn: ExtractorFn,
    *,
    model: str = "gpt-5.1",
    data_dir: Path | None = None,
    output_dir: Path,
) -> list[InferenceResult]:
    """Run the extractor on each scenario's PDF and collect results."""
    pdf_dir = data_dir or _DATA_DIR
    out_dir = output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[InferenceResult] = []
    for entry in dataset:
        pdf_path = pdf_dir / entry.file_name
        if not pdf_path.exists():
            print(
                f"WARNING: PDF not found, skipping: {pdf_path}",
                file=sys.stderr,
            )
            continue

        print(
            f"\n{'=' * 60}\n"
            f"Scenario:  {entry.id} — {entry.query}\n"
            f"PDF:       {pdf_path.name}\n"
            f"Model:     {model}\n"
            f"{'=' * 60}",
            file=sys.stderr,
        )

        max_retries = 5
        for attempt in range(max_retries):
            try:
                extracted = extractor_fn(
                    pdf_path=pdf_path,
                    prompt=entry.prompt,
                    model=model,
                    schema_name=entry.schema_name,
                )
                break
            except Exception as exc:
                is_rate_limit = "429" in str(exc) or "RateLimitError" in type(exc).__name__
                is_json_error = isinstance(exc, json.JSONDecodeError)
                is_retryable = is_rate_limit or is_json_error
                if is_retryable and attempt < max_retries - 1:
                    wait = 2 ** attempt * 15  # 15, 30, 60, 120, 240s
                    reason = "Rate limited" if is_rate_limit else "Bad JSON response"
                    print(
                        f"  {reason}, retrying in {wait}s "
                        f"(attempt {attempt + 1}/{max_retries}) …",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
                else:
                    raise

        # Persist raw extraction
        out_file = out_dir / f"{entry.id}_extracted.json"
        out_file.write_text(json.dumps(extracted, indent=2))
        print(f"  Saved: {out_file}", file=sys.stderr)

        results.append(InferenceResult(entry=entry, response=extracted))

    return results


# ---------------------------------------------------------------------------
# Evaluation via azure-ai-evaluation SDK
# ---------------------------------------------------------------------------


def _write_eval_jsonl(
    results: list[InferenceResult],
    output_dir: Path,
) -> Path:
    """Write inference results to a JSONL file consumed by ``evaluate()``."""
    path = output_dir / "eval_data.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in results:
            row = {
                "expected": r.entry.ground_truth
                if isinstance(r.entry.ground_truth, dict)
                else r.entry.ground_truth,
                "actual": r.response
                if isinstance(r.response, dict)
                else r.response,
                "scenario": r.entry.id,
                "document": r.entry.file_name,
            }
            f.write(json.dumps(row) + "\n")
    print(f"Wrote {len(results)} rows to {path}", file=sys.stderr)
    return path


def _run_evaluation(
    results: list[InferenceResult],
    *,
    output_dir: Path,
    foundry_project_endpoint: str | None = None,
) -> EvaluationResult:
    """Run leaf-level hit-rate evaluation and optionally log to Foundry."""
    data_path = _write_eval_jsonl(results, output_dir)
    output_path = output_dir / "eval_results.json"

    eval_result = evaluate(
        data=str(data_path),
        evaluators={"hit_rate": esg_hit_rate},
        evaluator_config={
            "hit_rate": {
                "column_mapping": {
                    "expected": "${data.expected}",
                    "actual": "${data.actual}",
                }
            }
        },
        evaluation_name="esg-hit-rate",
        azure_ai_project=foundry_project_endpoint,
        output_path=str(output_path),
    )

    # -- summary --------------------------------------------------------------
    print(f"\n{'=' * 70}", file=sys.stderr)
    pprint(eval_result.get("metrics", {}), stream=sys.stderr)
    print("=" * 70, file=sys.stderr)

    studio_url = eval_result.get("studio_url")
    if studio_url:
        print(f"\nFoundry portal: {studio_url}", file=sys.stderr)

    # -- per-scenario detail ---------------------------------------------------
    for row in eval_result.get("rows", []):
        scenario = row.get("inputs.scenario", "?")
        doc = row.get("inputs.document", "?")
        score = row.get("outputs.hit_rate.hit_rate", "?")
        expected_obj = row.get("inputs.expected", {})
        actual_obj = row.get("inputs.actual", {})

        hits, total = _count_leaf_matches(expected_obj, actual_obj)

        print(f"\n  {scenario} ({doc})", file=sys.stderr)
        print(
            f"    Hit rate: {score}  ({hits}/{total} leaf values)",
            file=sys.stderr,
        )

    print(f"\nDetailed results saved to {output_path}", file=sys.stderr)
    return eval_result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(
    *,
    extractor_fn: ExtractorFn,
    model: str = "gpt-5.1",
    output_dir: Path | None = None,
    foundry_project_endpoint: str | None = None,
) -> Path:
    """Run the full ESG evaluation loop.

    Parameters
    ----------
    extractor_fn:
        Callable ``(pdf_path, prompt, *, model) -> dict``.
    model:
        Model deployment name passed to the extractor.
    output_dir:
        Base output directory.  Defaults to ``<project_root>/output/esg``.
    foundry_project_endpoint:
        If provided, evaluation results are logged to the Foundry portal.

    Returns
    -------
    Path
        The timestamped output directory containing ``eval_results.json``.
    """
    extraction_method = _get_extraction_method(extractor_fn)

    base_output = output_dir or (_PROJECT_ROOT / "output" / "esg")
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    model_slug = model.replace(".", "-").replace(" ", "_")
    run_output = base_output / extraction_method / f"{timestamp}_{model_slug}"
    run_output.mkdir(parents=True, exist_ok=True)
    print(f"==> Output directory: {run_output}", file=sys.stderr)

    dataset = _load_dataset()

    print("==> Running inference …", file=sys.stderr)
    results = _run_inference(dataset, extractor_fn, model=model, output_dir=run_output)

    print("==> Running evaluation …", file=sys.stderr)
    _run_evaluation(
        results,
        output_dir=run_output,
        foundry_project_endpoint=foundry_project_endpoint,
    )

    return run_output

