"""
Evaluation loop for 10-K financial data extraction.

1. Loads ground-truth dataset from data/10k/evaluation_dataset.json.
2. Runs the extraction pipeline (DI → tables → financial matching) on each
   filtered PDF.
3. Runs the hit-rate evaluator locally via the azure-ai-evaluation SDK.
4. Logs results to the Foundry project for portal visibility.

Usage:
    uv run python -m src.ten_k.evaluate
    uv run python -m src.ten_k.evaluate --inferencer llm
    uv run python -m src.ten_k.evaluate --inferencer llm --model gpt-4.1-191849
"""

import json
import sys
from pathlib import Path
from pprint import pprint

from azure.ai.evaluation import EvaluationResult, evaluate
from pydantic import BaseModel

from ten_k.extraction import Extraction
from ten_k.hit_rate import _values_match, hit_rate
from ten_k.inferencer import Inferencer
from ten_k.llm_extract import LLMInferencer
from ten_k.llm_extract_unstructured import LLMUnstructuredInferencer
from ten_k.table_match import TableMatchInferencer

class EvaluationEntry(BaseModel):
    sheet_name: str
    year: int
    ground_truth: Extraction
    document: str
    filtered_pages: list[int]
    source_pdf: str


class InferenceResult(BaseModel):
    """Pairs an evaluation entry with its extracted (or dummy) response."""

    entry: EvaluationEntry
    response: Extraction


# ---------------------------------------------------------------------------
# Dataset – loaded from the ground-truth JSON
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DATASET_PATH = _PROJECT_ROOT / "data" / "10k" / "evaluation_dataset.json"


def load_dataset() -> list[EvaluationEntry]:
    """Load the ground-truth evaluation dataset from disk."""
    return [
        EvaluationEntry.model_validate(item)
        for item in json.loads(_DATASET_PATH.read_text())
    ]


def run_inference(
    dataset: list[EvaluationEntry],
    *,
    inferencer: Inferencer,
) -> list[InferenceResult]:
    """Run the extraction pipeline on each entry's filtered PDF.

    Applies the given *inferencer* to every entry in the dataset.
    """

    data_dir = _PROJECT_ROOT / "data" / "10k"

    results: list[InferenceResult] = []
    for entry in dataset:
        pdf_path = data_dir / "raw_documents" / f"filtered_{entry.source_pdf}"
        extraction = inferencer.extract(
            pdf_path,
            target_year=str(entry.year),
        )
        results.append(InferenceResult(entry=entry, response=extraction))

    return results


# ---------------------------------------------------------------------------
# Local evaluation via azure-ai-evaluation SDK
# ---------------------------------------------------------------------------


def write_eval_jsonl(results: list[InferenceResult]) -> Path:
    """Write inference results to a JSONL file for ``evaluate()``."""
    path = _PROJECT_ROOT / "data" / "10k" / "output" / "eval_data.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in results:
            row = {
                "expected": r.entry.ground_truth.model_dump(),
                "actual": r.response.model_dump(),
                "document": r.entry.source_pdf,
                "year": r.entry.year,
            }
            f.write(json.dumps(row) + "\n")
    print(f"Wrote {len(results)} rows to {path}", file=sys.stderr)
    return path


def run_evaluation(
    results: list[InferenceResult],
    *,
    evaluation_name: str,
    foundry_project_endpoint: str | None = None,
) -> EvaluationResult:
    """Run hit-rate evaluation locally and optionally log to Foundry."""
    data_path = write_eval_jsonl(results)
    output_path = _PROJECT_ROOT / "data" / "10k" / "output" / "eval_results.json"

    eval_result = evaluate(
        data=str(data_path),
        evaluators={"hit_rate": hit_rate},
        evaluator_config={
            "hit_rate": {
                "column_mapping": {
                    "expected": "${data.expected}",
                    "actual": "${data.actual}",
                }
            }
        },
        evaluation_name=evaluation_name,
        azure_ai_project=foundry_project_endpoint,
        output_path=str(output_path),
    )

    # -- print summary -------------------------------------------------------
    print(f"\n{'=' * 70}", file=sys.stderr)
    pprint(eval_result.get("metrics", {}), stream=sys.stderr)
    print("=" * 70, file=sys.stderr)

    studio_url = eval_result.get("studio_url")
    if studio_url:
        print(f"\nFoundry portal: {studio_url}", file=sys.stderr)

    # -- per-document detail --------------------------------------------------
    for row in eval_result.get("rows", []):
        doc = row.get("inputs.document", "?")
        year = row.get("inputs.year", "?")
        score = row.get("outputs.hit_rate.hit_rate", "?")
        expected_dict: dict = row.get("inputs.expected", {})
        actual_dict: dict = row.get("inputs.actual", {})

        missed = [
            k
            for k in expected_dict
            if not _values_match(expected_dict[k], actual_dict.get(k))
        ]
        total = len(expected_dict)
        hits = total - len(missed)

        print(f"\n  {doc} (year={year})", file=sys.stderr)
        print(f"    Hit rate: {score}  ({hits}/{total} fields)", file=sys.stderr)
        if missed:
            print(f"    Missed:   {missed}", file=sys.stderr)

    print(f"\nDetailed results saved to {output_path}", file=sys.stderr)
    return eval_result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(
    *,
    evaluation_name: str,
    foundry_project_endpoint: str | None = None,
    inferencer: Inferencer | None = None,
) -> None:
    dataset = load_dataset()

    if inferencer is None:
        output_dir = _PROJECT_ROOT / "data" / "10k" / "output"
        inferencer = TableMatchInferencer(output_dir=output_dir)

    print(f"==> Running inference with {type(inferencer).__name__} …", file=sys.stderr)
    results = run_inference(dataset, inferencer=inferencer)

    print("==> Running local evaluation …", file=sys.stderr)
    run_evaluation(
        results,
        evaluation_name=evaluation_name,
        foundry_project_endpoint=foundry_project_endpoint,
    )


if __name__ == "__main__":
    import argparse

    from dotenv import dotenv_values

    from models import Model, supports_structured_output

    parser = argparse.ArgumentParser(description="10-K extraction evaluation.")
    parser.add_argument(
        "--inferencer",
        choices=["table-match", "llm"],
        default="table-match",
        help="Inferencer to evaluate (default: table-match).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        choices=[m.value for m in Model],
        help="Model deployment for the LLM inferencer.",
    )
    parser.add_argument(
        "--name",
        default="10k-hit-rate",
        help="Evaluation run name (shown in Foundry portal, default: 10k-hit-rate).",
    )
    args = parser.parse_args()

    config = dotenv_values()
    foundry_project_endpoint = config.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")

    output_dir = _PROJECT_ROOT / "data" / "10k" / "output"
    if args.inferencer == "llm":
        model = Model(args.model) if args.model else Model.GPT_5_1
        if supports_structured_output(model):
            selected_inferencer: Inferencer = LLMInferencer(
                output_dir=output_dir, model=model,  # type: ignore[arg-type]
            )
        else:
            selected_inferencer = LLMUnstructuredInferencer(
                output_dir=output_dir, model=model,  # type: ignore[arg-type]
            )
    else:
        selected_inferencer = TableMatchInferencer(output_dir=output_dir)

    main(
        evaluation_name=args.name,
        foundry_project_endpoint=foundry_project_endpoint,
        inferencer=selected_inferencer,
    )
