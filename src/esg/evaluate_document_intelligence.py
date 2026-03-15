"""Evaluate ESG extraction using the Document Intelligence pipeline.

Runs the full evaluate loop with ``run_extraction_document_intelligence.extract``
as the extractor.

Usage::

    uv run python -m esg.evaluate_document_intelligence
    uv run python -m esg.evaluate_document_intelligence --model gpt-4.1-191849
"""

import argparse

from dotenv import dotenv_values
from functools import partial
from typing import cast

from esg.evaluate import ExtractorFn, run
from esg.run_extraction_document_intelligence import extract

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Evaluate ESG extraction (Document Intelligence pipeline)"
    )
    parser.add_argument(
        "--model",
        default="gpt-5.1",
        help="Model deployment name (default: gpt-5.1)",
    )
    args = parser.parse_args()

    config = dotenv_values()
    endpoint = config.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_FOUNDRY_PROJECT_ENDPOINT not set in .env")

    extractor = cast(ExtractorFn, partial(extract, foundry_project_endpoint=endpoint))

    run(
        extractor_fn=extractor,
        model=args.model,
        foundry_project_endpoint=endpoint,
    )
