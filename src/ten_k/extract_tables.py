"""Extract tables from PDF documents using Azure Document Intelligence.

Uses the prebuilt-layout model for table extraction.
Converts raw DI output into clean row-based JSON tables.
Shared infrastructure used by all 10-K inferencer implementations.

Usage:
    uv run python -m ten_k.extract_tables <pdf_or_dir> [--output-dir DIR] [--force]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.identity import AzureCliCredential

DI_ENDPOINT = "https://di-foundryhack-main.cognitiveservices.azure.com/"


def analyze_pdf(pdf_path: Path) -> dict:
    """Send PDF to Document Intelligence and return the analysis result."""
    with AzureCliCredential() as credential:
        client = DocumentIntelligenceClient(
            endpoint=DI_ENDPOINT, credential=credential
        )
        with open(pdf_path, "rb") as f:
            poller = client.begin_analyze_document(
                model_id="prebuilt-layout",
                body=AnalyzeDocumentRequest(bytes_source=f.read()),
            )
        return poller.result().as_dict()


def analyze_pdf_markdown(pdf_path: Path) -> str:
    """Send PDF to Document Intelligence and return markdown content."""
    with AzureCliCredential() as credential:
        client = DocumentIntelligenceClient(
            endpoint=DI_ENDPOINT, credential=credential
        )
        with open(pdf_path, "rb") as f:
            poller = client.begin_analyze_document(
                model_id="prebuilt-layout",
                body=AnalyzeDocumentRequest(bytes_source=f.read()),
                output_content_format="markdown",
            )
        return poller.result().content


def _build_grid(table: dict) -> list[list[str]]:
    """Build a 2D grid from a Document Intelligence table."""
    rows = table["rowCount"]
    cols = table["columnCount"]
    grid: list[list[str]] = [[""] * cols for _ in range(rows)]
    for cell in table["cells"]:
        grid[cell["rowIndex"]][cell["columnIndex"]] = cell.get("content", "").strip()
    return grid


def _table_pages(table: dict) -> list[int]:
    """Get sorted page numbers for a table."""
    pages: set[int] = set()
    for cell in table["cells"]:
        for region in cell.get("boundingRegions", []):
            pages.add(region["pageNumber"])
    return sorted(pages)


def tables_to_json(analyze_result: dict) -> list[dict]:
    """Convert Document Intelligence tables into clean row-based JSON.

    Handles multi-row headers (common in financial statements where row 0
    has a period description and row 1 has actual year labels).
    """
    tables = analyze_result.get("tables", [])
    clean_tables = []

    for idx, table in enumerate(tables):
        grid = _build_grid(table)
        if not grid:
            continue

        # Detect header rows: check if row 1 contains year values like "2023"
        header_end = 1
        if len(grid) > 1:
            row1_vals = [c.strip() for c in grid[1] if c.strip()]
            if row1_vals and all(len(v) == 4 and v.isdigit() for v in row1_vals):
                header_end = 2

        headers_raw = grid[:header_end]
        data_rows = grid[header_end:]

        # Build composite headers by joining multi-row header cells
        col_count = table["columnCount"]
        headers = []
        for c in range(col_count):
            parts = [headers_raw[r][c] for r in range(len(headers_raw)) if headers_raw[r][c]]
            headers.append(" | ".join(parts) if parts else f"col_{c}")

        rows = []
        for row in data_rows:
            row_dict = {}
            for c, header in enumerate(headers):
                row_dict[header] = row[c] if c < len(row) else ""
            rows.append(row_dict)

        clean_tables.append({
            "table_index": idx,
            "pages": _table_pages(table),
            "rowCount": table["rowCount"],
            "columnCount": col_count,
            "headers": headers,
            "rows": rows,
        })

    return clean_tables


def process_pdf(
    pdf_path: Path,
    *,
    output_dir: Path | None = None,
    force: bool = False,
) -> tuple[Path, Path]:
    """Run DI analysis + table extraction for one PDF.

    Returns:
        (raw_path, tables_path) — paths to the two output files.
    """
    if output_dir is None:
        output_dir = pdf_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = pdf_path.stem
    log = lambda msg: print(msg, file=sys.stderr)  # noqa: E731

    # Step 1: Analyze with Document Intelligence (or reuse cached result)
    raw_path = output_dir / f"{stem}.di_raw.json"
    if raw_path.exists() and not force:
        log(f"[{pdf_path.name}] Reusing cached DI result")
        with open(raw_path) as f:
            result = json.load(f)
    else:
        log(f"[{pdf_path.name}] Sending to Document Intelligence...")
        result = analyze_pdf(pdf_path)
        with open(raw_path, "w") as f:
            json.dump(result, f, indent=2)
        log(f"[{pdf_path.name}] Raw DI result → {raw_path.name}")

    # Step 2: Extract clean tables
    clean_tables = tables_to_json(result)
    tables_path = output_dir / f"{stem}.tables.json"
    with open(tables_path, "w") as f:
        json.dump(clean_tables, f, indent=2)
    log(f"[{pdf_path.name}] {len(clean_tables)} tables → {tables_path.name}")

    return raw_path, tables_path


def process_pdf_markdown(
    pdf_path: Path,
    *,
    output_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Run DI markdown extraction for one PDF.

    Returns:
        Path to the cached markdown file.
    """
    if output_dir is None:
        output_dir = pdf_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = pdf_path.stem
    log = lambda msg: print(msg, file=sys.stderr)  # noqa: E731

    md_path = output_dir / f"{stem}.di.md"
    if md_path.exists() and not force:
        log(f"[{pdf_path.name}] Reusing cached DI markdown")
    else:
        log(f"[{pdf_path.name}] Sending to Document Intelligence (markdown)...")
        markdown = analyze_pdf_markdown(pdf_path)
        md_path.write_text(markdown, encoding="utf-8")
        log(f"[{pdf_path.name}] Markdown → {md_path.name}")

    return md_path


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


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract tables from PDF(s) via Azure Document Intelligence."
    )
    parser.add_argument("input", type=Path, help="A PDF file or directory of PDFs.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory.")
    parser.add_argument("--force", action="store_true", help="Re-analyze even if cached.")
    args = parser.parse_args()

    pdfs = _collect_pdfs(args.input)
    if not pdfs:
        sys.exit(1)

    out = args.output_dir or (args.input if args.input.is_dir() else args.input.parent)
    for pdf_path in pdfs:
        process_pdf(pdf_path, output_dir=out, force=args.force)

    print(f"Done — processed {len(pdfs)} PDF(s).", file=sys.stderr)


if __name__ == "__main__":
    main()
