"""Extract specific financial line items from clean table JSON.

Reads the schema from schema.json and matches table row labels against
the defined aliases to produce structured financial output that mirrors
the ground truth format.

Usage:
    uv run python -m src.ten_k.extract_financials <tables_json> [--year YEAR] [--schema PATH]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_SCHEMA = SCRIPTS_DIR / "schema.json"


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

def load_schema(schema_path: Path) -> dict:
    """Load schema.json preserving the nested structure.

    Returns the raw schema dict (minus metadata keys like $comment).
    Structure: {category: {subcategory: {item_name: [aliases]}}}.
    """
    with open(schema_path) as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("$")}


def flatten_schema(schema: dict) -> dict[str, list[str]]:
    """Flatten the nested schema into {item_name: [aliases]} for matching."""
    flat: dict[str, list[str]] = {}
    for category in schema.values():
        for subcategory in category.values():
            for item_name, aliases in subcategory.items():
                flat[item_name] = aliases
    return flat


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation (keep spaces) for fuzzy matching."""
    return " ".join(
        "".join(c for c in word if c.isalnum()) for word in text.lower().split()
    ).strip()


def _parse_number(text: str) -> float | None:
    """Parse a financial number: handles ($), commas, $, -, \u2014."""
    t = text.strip().replace(",", "").replace("$", "").replace("\u2014", "").replace(" ", "")
    if not t or t == "-":
        return None
    negative = False
    if t.startswith("(") and t.endswith(")"):
        negative = True
        t = t[1:-1]
    try:
        val = float(t)
        return -val if negative else val
    except ValueError:
        return None


def _match_score(alias_norm: str, label_norm: str) -> int:
    """Return a match score (higher is better), 0 = no match.

    Exact match scores highest, then substring match scored by length.
    """
    if alias_norm == label_norm:
        return 1000
    if alias_norm in label_norm:
        return len(alias_norm)
    return 0


def extract_financial_items(
    clean_tables: list[dict],
    flat_schema: dict[str, list[str]],
    target_year: str | None = None,
) -> tuple[dict[str, float | None], list[int]]:
    """Search all tables for target financial line items defined in schema.

    Args:
        clean_tables: Output of extract_tables.tables_to_json.
        flat_schema: Flattened {standardized_name: [aliases]} dict.
        target_year: If set, prefer the column whose header contains this year.

    Returns:
        Tuple of (flat dict {item_name: value_or_None}, sorted list of matched pages).
    """
    results: dict[str, float | None] = {k: None for k in flat_schema}
    matched_pages: set[int] = set()

    # Pre-normalize all aliases: (normalized_alias, std_name, priority)
    alias_list: list[tuple[str, str, int]] = []
    for std_name, aliases in flat_schema.items():
        for priority, alias in enumerate(aliases):
            alias_list.append((_normalize(alias), std_name, priority))

    for table in clean_tables:
        table_pages = table.get("pages", [])
        headers = table["headers"]

        # Determine which column index corresponds to the target year
        year_col: int | None = None
        if target_year:
            for ci, h in enumerate(headers):
                if target_year in h:
                    year_col = ci
                    break

        for row in table["rows"]:
            vals = list(row.values())
            if not vals:
                continue
            label_norm = _normalize(vals[0])
            if not label_norm:
                continue

            # Find the best matching target item
            best_name: str | None = None
            best_score = 0
            for alias_norm, std_name, priority in alias_list:
                score = _match_score(alias_norm, label_norm)
                if score > 0:
                    adj_score = score * 100 - priority
                    if adj_score > best_score:
                        best_score = adj_score
                        best_name = std_name

            if best_name is None or results[best_name] is not None:
                continue

            # Extract value — prefer the target year column
            if year_col is not None and year_col < len(vals):
                parsed = _parse_number(vals[year_col])
                if parsed is not None:
                    results[best_name] = parsed
                    matched_pages.update(table_pages)
                    continue

    return results, sorted(matched_pages)


def _nest_results(
    flat_results: dict[str, float | None],
    schema: dict,
) -> dict:
    """Reshape flat extraction results into the nested ground truth structure.

    schema structure: {category: {subcategory: {item: aliases}}}
    output structure: {category: {subcategory: {item: value}}}
    """
    nested: dict = {}
    for category, subcategories in schema.items():
        nested[category] = {}
        for subcategory, items in subcategories.items():
            nested[category][subcategory] = {}
            for item_name in items:
                nested[category][subcategory][item_name] = flat_results.get(item_name)
    return nested


def process_tables(
    tables_path: Path,
    *,
    target_year: str | None = None,
    schema_path: Path = DEFAULT_SCHEMA,
    output_path: Path | None = None,
) -> dict:
    """Run financial extraction on a tables.json file.

    Returns:
        Dict in ground truth format with nested categories.
    """
    schema = load_schema(schema_path)
    flat_schema = flatten_schema(schema)

    with open(tables_path) as f:
        clean_tables = json.load(f)

    flat_results, filtered_pages = extract_financial_items(
        clean_tables, flat_schema, target_year=target_year,
    )
    nested_results = _nest_results(flat_results, schema)

    # Derive source filename from tables path (strip .tables.json)
    stem = tables_path.stem
    if stem.endswith(".tables"):
        stem = stem[: -len(".tables")]
    source_file = f"{stem}.pdf"

    year_int = int(target_year) if target_year and target_year.isdigit() else None

    # Build sheet_name: {YYYY}1231_{company_part}
    # Strip trailing _Q*_Annual_FS / _Q*_FS etc. to get the company portion
    company_part = re.sub(r"_Q\d+(_Annual)?_FS$", "", stem)
    year_str = target_year or ""
    sheet_name = f"{year_str}1231_{company_part}" if year_str else company_part

    output = {
        "document": f"data/k10/raw_documents/filtered_{stem}.pdf",
        "filtered_pages": filtered_pages,
        "sheet_name": sheet_name,
        "source_pdf": source_file,
        "year": year_int,
        "extracted_information": nested_results,
    }

    found = sum(1 for v in flat_results.values() if v is not None)
    total = len(flat_results)

    if output_path is not None:
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"[{source_file}] {found}/{total} line items → {output_path.name}", file=sys.stderr)
    else:
        print(f"[{source_file}] {found}/{total} line items", file=sys.stderr)

    return output


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract financial line items from tables JSON using a schema."
    )
    parser.add_argument("tables", type=Path, help="A .tables.json file (or directory of them).")
    parser.add_argument("--year", type=str, default=None, help="Target fiscal year (e.g. 2023).")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Schema JSON file.")
    args = parser.parse_args()

    # Collect input files
    if args.tables.is_file():
        files = [args.tables]
    elif args.tables.is_dir():
        files = sorted(args.tables.glob("*.tables.json"))
        if not files:
            print(f"No .tables.json files found in {args.tables}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Not a file or directory: {args.tables}", file=sys.stderr)
        sys.exit(1)

    all_results = []
    for tables_path in files:
        stem = tables_path.stem
        if stem.endswith(".tables"):
            stem = stem[: -len(".tables")]
        out_path = tables_path.parent / f"{stem}.financials.json"
        result = process_tables(
            tables_path, target_year=args.year, schema_path=args.schema,
            output_path=out_path,
        )
        all_results.append(result)

    # Print combined results to stdout
    if len(all_results) == 1:
        json.dump(all_results[0], sys.stdout, indent=2)
    else:
        json.dump(all_results, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
