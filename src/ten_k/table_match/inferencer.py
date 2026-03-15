"""Table-matching inferencer: PDF → Document Intelligence → schema alias matching.

Extracts financial line items by sending PDFs through Azure Document
Intelligence for table extraction, then matching row labels against
schema-defined aliases.

Usage:
    uv run python -m src.ten_k.table_match_inferencer data/ --year 2023
    uv run python -m src.ten_k.table_match_inferencer data/report.pdf --year 2023 --output-dir results/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ten_k.table_match.extract_financials import DEFAULT_SCHEMA, process_tables
from ten_k.extract_tables import process_pdf as extract_tables_for_pdf
from ten_k.extraction import Extraction


class TableMatchInferencer:
    """Extracts financials by matching DI table rows against schema aliases.

    Constructor args are implementation-specific configuration; the
    :meth:`extract` method fulfils the :class:`~ten_k.inferencer.Inferencer`
    protocol.
    """

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        schema_path: Path = DEFAULT_SCHEMA,
        force: bool = False,
    ) -> None:
        self.output_dir = output_dir
        self.schema_path = schema_path
        self.force = force

    def extract(
        self,
        pdf_path: Path,
        *,
        target_year: str,
    ) -> Extraction:
        """Extract financial data from a single 10-K PDF.

        Runs DI table extraction followed by schema-based alias matching.
        """
        out = self.output_dir if self.output_dir is not None else pdf_path.parent

        # Step 1+2: Document Intelligence + table extraction
        _raw_path, tables_path = extract_tables_for_pdf(
            pdf_path, output_dir=out, force=self.force
        )

        # Step 3: Financial line item extraction
        result = process_tables(
            tables_path,
            target_year=target_year,
            schema_path=self.schema_path,
        )

        return Extraction.model_validate(result["extracted_information"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_pdfs(path: Path) -> list[Path]:
    """Return a list of PDF files from a path (single file or directory)."""
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    if path.is_dir():
        pdfs = sorted(path.glob("*.pdf"))
        if not pdfs:
            print(f"No PDF files found in {path}", file=sys.stderr)
        return pdfs
    print(f"Not a PDF file or directory: {path}", file=sys.stderr)
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Full pipeline: PDF → Document Intelligence → Tables → Financials."
    )
    parser.add_argument("input", type=Path, help="A PDF file or directory of PDFs.")
    parser.add_argument("--year", type=str, default=None, help="Target fiscal year (e.g. 2023).")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory.")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Schema JSON file.")
    parser.add_argument("--force", action="store_true", help="Re-analyze with DI even if cached.")
    args = parser.parse_args()

    pdfs = _collect_pdfs(args.input)
    if not pdfs:
        sys.exit(1)

    if args.output_dir:
        out = args.output_dir
    else:
        # Default: mirror input path under output/ (e.g. data/input/X → data/output/X)
        input_dir = args.input if args.input.is_dir() else args.input.parent
        parts = input_dir.resolve().parts
        if "input" in parts:
            idx = parts.index("input")
            out = Path(*parts[:idx]) / "output" / Path(*parts[idx + 1 :])
        else:
            out = input_dir

    inferencer = TableMatchInferencer(
        output_dir=out,
        schema_path=args.schema,
        force=args.force,
    )

    all_results = []
    for pdf_path in pdfs:
        print(f"\n{'=' * 60}", file=sys.stderr)
        result = inferencer.extract(pdf_path, target_year=args.year)
        all_results.append(result)

    # Write single combined output file
    out.mkdir(parents=True, exist_ok=True)
    combined_path = out / "financials.json"
    with open(combined_path, "w") as f:
        json.dump(all_results, f, indent=2)

    # Summary
    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"Processed {len(all_results)} PDF(s) → {combined_path}", file=sys.stderr)
    for r in all_results:
        gt = r["extracted_information"]
        total = found = 0
        for cat in gt.values():
            for subcat in cat.values():
                for v in subcat.values():
                    total += 1
                    if v is not None:
                        found += 1
        print(f"  {r['source_pdf']}: {found}/{total} items", file=sys.stderr)


if __name__ == "__main__":
    main()
