"""Integration tests — extract from real PDFs and verify against ground truth.

These tests call Azure Foundry and require:
- Valid ``az login`` credentials
- ``AZURE_FOUNDRY_PROJECT_ENDPOINT`` set in ``.env``
- PDF files present in ``ESG-files/data/``
- Ground truth JSON files in ``ESG-files/ground_truth/``

Run with::

    uv run pytest tests/esg/test_integration.py -v

Set the ``MODEL`` environment variable to override the default model::

    MODEL=gpt-4.1-191849 uv run pytest tests/esg/test_integration.py -v
"""

import json
import os
import sys
from pathlib import Path

import pytest

from evaluation import compare_with_ground_truth, print_evaluation
from src.esg.run_extraction_custom import _extract_with_schema as extract

pytestmark = pytest.mark.integration

DATA_DIR = Path("ESG-files/data")
GT_DIR = Path("ESG-files/ground_truth")


def _get_endpoint() -> str:
    """Return the Foundry endpoint or skip if unavailable."""
    from dotenv import dotenv_values

    config = dotenv_values()
    endpoint = config.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        pytest.skip("AZURE_FOUNDRY_PROJECT_ENDPOINT not set in .env")
    return endpoint


def _run_scenario(
    pdf_name: str,
    gt_json_path: Path,
    *,
    model: str | None = None,
    modes: tuple[str, ...] = ("file", "vision", "text"),
    output_dir: str = "output",
) -> None:
    """Load PDF + ground truth, extract, compare, assert 100% accuracy."""
    endpoint = _get_endpoint()
    model = model or os.environ.get("MODEL", "gpt-5.1")

    pdf_path = DATA_DIR / pdf_name
    if not pdf_path.exists():
        pytest.skip(f"PDF not available: {pdf_path}")
    if not gt_json_path.exists():
        pytest.skip(f"Ground truth not available: {gt_json_path}")

    with open(gt_json_path) as f:
        gt_data = json.load(f)

    prompt = gt_data["prompt"]
    ground_truth = gt_data["ground_truth"]
    page_hint = gt_data.get("page")

    pdf_contents = [pdf_path.read_bytes()]

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"Scenario:  {gt_json_path.stem}", file=sys.stderr)
    print(f"PDF:       {pdf_name}", file=sys.stderr)
    print(f"Model:     {model}", file=sys.stderr)
    print(f"Modes:     {modes}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    extracted = extract(
        pdf_path=pdf_path,
        prompt=prompt,
        schema=ground_truth,
        foundry_project_endpoint=endpoint,
        model=model,
        modes=modes,
        page_hint=page_hint,
    )

    # Save extraction output for inspection
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{gt_json_path.stem}_extracted.json"
    out_file.write_text(json.dumps(extracted, indent=2))
    print(f"  Saved: {out_file}", file=sys.stderr)

    # Evaluate
    evaluation = compare_with_ground_truth(extracted, ground_truth)

    eval_file = out_dir / f"{gt_json_path.stem}_evaluation.json"
    eval_file.write_text(json.dumps(evaluation, indent=2))
    print(f"  Saved: {eval_file}", file=sys.stderr)

    print_evaluation(evaluation)

    assert evaluation["summary"]["accuracy_percent"] == 100.0, (
        f"Accuracy {evaluation['summary']['accuracy_percent']}% — "
        f"expected 100% ({evaluation['summary']['matches']}/"
        f"{evaluation['summary']['total_fields']} fields matched)"
    )


class TestScenario1:
    """BASF Risk Assessment — KEY ISSUE ASSESSMENT extraction."""

    def test_basf_risk_assessment(self) -> None:
        _run_scenario(
            "ESG_Ratings_Report_BASF.pdf",
            GT_DIR / "Scenario_1" / "BASF_Risk_Assessment.json",
            output_dir="output/scenario1",
        )


class TestScenario2:
    """BASF Description and Practice table extraction."""

    def test_basf_description_and_practice(self) -> None:
        _run_scenario(
            "ESG_Ratings_Report_BASF.pdf",
            GT_DIR / "Scenario_2" / "BASF_Description_and_Practice.json",
            output_dir="output/scenario2",
        )


class TestScenario3:
    """Cargill Controversies Case Assessment extraction."""

    def test_cargill_case_assessment(self) -> None:
        _run_scenario(
            "Cargill_Controversies_Environment.pdf",
            GT_DIR / "Scenario_3" / "Cargill_Controversies_Case_Assessment.json",
            output_dir="output/scenario3",
        )
