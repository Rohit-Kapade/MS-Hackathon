"""Generic PDF structured data extraction service.

Extracts structured data from one or more PDF documents using Azure Foundry
models. Accepts raw PDF bytes and a natural-language prompt — the service has
no knowledge of ground truth files or specific data sources.

Three complementary input modes are used together by default for maximum
accuracy:

- ``file``   — sends the PDF natively via the Responses API.
- ``vision`` — renders each page as a high-DPI PNG image.
- ``text``   — extracts plain text with pymupdf.

CLI Usage
---------
Basic extraction (all three modes, default model ``gpt-5.1``)::

    uv run python src/esg/run_extraction.py \\
        --pdf ESG-files/data/BASF_Carbon_Emission.pdf \\
        --prompt "Extract RISK EXPOSURE and RISK MANAGEMENT assessments" \\
        --schema-file src/prompt/controversies_schema.json \\
        --output output/result.json

Specify a different model::

    uv run python src/esg/run_extraction.py \\
        --pdf ESG-files/data/BASF_Carbon_Emission.pdf \\
        --prompt "Extract carbon emission data" \\
        --schema-file src/prompt/controversies_schema.json \\
        --output output/result.json \\
        --model gpt-4.1-191849

Use only the vision and file modes (skip text extraction)::

    uv run python src/esg/run_extraction.py \\
        --pdf ESG-files/data/BASF_Carbon_Emission.pdf \\
        --prompt "Extract carbon emission data" \\
        --schema-file src/prompt/controversies_schema.json \\
        --mode file vision

Extract from multiple PDFs at once::

    uv run python src/esg/run_extraction.py \\
        --pdf doc1.pdf doc2.pdf \\
        --prompt "Extract controversy data" \\
        --schema-file src/prompt/controversies_schema.json \\
        --output output/combined.json

Focus on specific pages::

    uv run python src/esg/run_extraction.py \\
        --pdf ESG-files/data/BASF_Carbon_Emission.pdf \\
        --prompt "Extract risk scores" \\
        --schema-file src/prompt/controversies_schema.json \\
        --page-hint "3,4"

CLI Arguments
-------------
--pdf           One or more PDF file paths (required).
--prompt        Natural-language extraction instruction (required).
--schema-file   Path to JSON file describing the expected output structure
                (required). The values in this file are used as structural
                hints for the model — they are NOT sent as answers.
--output        Output JSON file path (default: output/result.json).
--model         Azure Foundry deployment name (default: gpt-5.1).
--mode          One or more of: file vision text (default: all three).
--page-hint     Optional comma-separated page numbers to focus on.

Environment
-----------
Requires ``AZURE_FOUNDRY_PROJECT_ENDPOINT`` to be set in a ``.env`` file.
Authentication is handled by ``AzureCliCredential`` — run ``az login`` once
before invoking the script.

Python API
----------
The ``extract()`` function is the public programmatic API::

    from src.esg.run_extraction_custom import extract

    result = extract(
        pdf_path="doc.pdf",
        prompt="Extract risk scores",
        schema_name="BASF_risk_assessment_schema.json",
    )

The ``foundry_project_endpoint`` parameter is optional. If not provided,
the function will read it from the ``AZURE_FOUNDRY_PROJECT_ENDPOINT``
environment variable.

Use ``_extract_with_schema()`` directly when you need to override ``modes``
or supply a ``page_hint``.  ``_load_schema()`` resolves a schema filename to
a data template (handling the JSON Schema → ground-truth fallback
automatically).
"""

import argparse
import base64
import json
import sys
import time as _time
from pathlib import Path

import fitz  # pymupdf
from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
from dotenv import dotenv_values

EXTRACTION_MODES = ("file", "vision", "text")

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "ESG-files" / "schemas"


# ---------------------------------------------------------------------------
# PDF utilities (bytes-based — no file path coupling)
# ---------------------------------------------------------------------------


def pdf_to_text(pdf_bytes: bytes) -> str:
    """Extract plain text from PDF bytes using pymupdf."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages: list[str] = []
    for num, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            pages.append(f"--- Page {num + 1} ---\n{text}")
    doc.close()
    full_text = "\n\n".join(pages)
    if not full_text.strip():
        raise ValueError("No text could be extracted from PDF")
    return full_text


def _extract_keywords(prompt: str, schema: dict | list) -> set[str]:
    """Derive search keywords from the prompt and schema keys.

    Returns lowercase terms ≥ 4 characters that are not common stopwords.
    Schema keys are split on whitespace, underscores, and hyphens.
    """
    _STOPWORDS = {
        "a", "an", "the", "and", "or", "in", "on", "at", "to", "for",
        "of", "is", "are", "was", "be", "that", "this", "with", "from",
        "by", "as", "it", "its", "not", "go", "get", "two", "fields",
        "extract", "section", "information", "field", "value", "values",
        "please", "only", "each", "into", "also", "both", "using", "about",
    }
    words: set[str] = set()

    for word in prompt.lower().split():
        cleaned = word.strip("'\".,;:!?&()/\\")
        if len(cleaned) >= 4 and cleaned not in _STOPWORDS:
            words.add(cleaned)

    def _collect_keys(obj: object) -> None:
        if isinstance(obj, dict):
            for k in obj:
                for part in k.lower().replace("_", " ").replace("-", " ").split():
                    if len(part) >= 4 and part not in _STOPWORDS:
                        words.add(part)
                _collect_keys(obj[k])
        elif isinstance(obj, list):
            for item in obj:
                _collect_keys(item)

    _collect_keys(schema)
    return words


def find_relevant_pages(
    pdf_contents: list[bytes],
    prompt: str,
    schema: dict | list,
    max_pages: int = 20,
) -> list[int]:
    """Return 0-based indices of pages most relevant to *prompt* and *schema*.

    Pages are scored by counting how many extracted keywords appear in their
    text. The top ``max_pages`` scoring pages are returned in document order.
    Returns an empty list when no keywords match any page (caller falls back
    to the full-document cap).
    """
    keywords = _extract_keywords(prompt, schema)
    if not keywords:
        return []

    scored: list[tuple[int, int]] = []  # (0-based page index, keyword-hit count)
    for pdf_bytes in pdf_contents:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for idx in range(len(doc)):
            text = str(doc[idx].get_text()).lower()
            if not text.strip():
                continue
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scored.append((idx, score))
        doc.close()

    if not scored:
        return []

    # Take the top-scoring pages and return them in original document order.
    scored.sort(key=lambda x: -x[1])
    top_indices = [idx for idx, _ in scored[:max_pages]]
    return sorted(top_indices)


def pdf_to_images(
    pdf_bytes: bytes,
    dpi: int = 200,
    pages: list[int] | None = None,
) -> list[str]:
    """Render PDF pages as base64-encoded PNGs.

    Args:
        pdf_bytes: Raw PDF bytes.
        dpi: Render resolution (default 200).
        pages: Optional list of 0-based page indices to render.
               Renders all pages when ``None``.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    page_indices = pages if pages is not None else list(range(len(doc)))
    images: list[str] = []
    for idx in page_indices:
        if 0 <= idx < len(doc):
            pix = doc[idx].get_pixmap(matrix=matrix)
            b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
            images.append(b64)
    doc.close()
    if not images:
        raise ValueError("No pages found in PDF")
    return images


# File-path convenience wrappers (used by tests and CLI)


def load_pdf_text(pdf_path: Path) -> str:
    """Extract plain text from a PDF file path."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    return pdf_to_text(pdf_path.read_bytes())


def load_pdf_images(pdf_path: Path, dpi: int = 200) -> list[str]:
    """Render each PDF page as base64 PNGs from a file path."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    return pdf_to_images(pdf_path.read_bytes(), dpi=dpi)


# ---------------------------------------------------------------------------
# Schema hint builder
# ---------------------------------------------------------------------------


def make_schema_hint(obj: object) -> object:
    """Build a placeholder schema from a value structure.

    Replaces leaf values with ``"..."`` so the model knows the expected
    shape without seeing the answers.  For lists, every item is rendered as
    a placeholder slot so the model knows exactly how many items to extract
    and which keys each slot requires (important for sections with different
    column names, e.g. 'Practice Score' vs 'Practices Score').
    """
    if isinstance(obj, dict):
        return {k: make_schema_hint(v) for k, v in obj.items()}
    if isinstance(obj, list):
        if not obj:
            return []
        return [make_schema_hint(item) for item in obj]
    return "..."


# ---------------------------------------------------------------------------
# Extraction (Responses API)
# ---------------------------------------------------------------------------


def _collect_dict_field_hints(
    gt: dict, prefix: str = ""
) -> dict[str, str]:
    """Return a mapping of dotted field path → example value for leaf fields
    that look like short codes or Y/N indicators (len ≤ 5, all uppercase or
    single-char).  These are the fields where the PDF may render the value as
    an icon or checkbox rather than readable text.
    """
    hints: dict[str, str] = {}
    for key, val in gt.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            hints.update(_collect_dict_field_hints(val, full_key))
        elif isinstance(val, str) and 1 <= len(val) <= 5 and val == val.upper():
            hints[full_key] = val
    return hints


def _extract_with_schema(
    pdf_path: str | Path,
    prompt: str,
    schema: dict | list,
    *,
    foundry_project_endpoint: str | None = None,
    model: str = "gpt-5.1",
    modes: tuple[str, ...] = ("file", "vision", "text"),
    page_hint: str | None = None,
) -> dict | list:
    """Low-level extraction that accepts a schema object directly.

    Use ``extract()`` for the standard ``ExtractorFn`` interface that
    takes a ``schema_name`` string instead.
    """
    # Read PDF file
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    pdf_bytes = pdf_path.read_bytes()
    pdf_filename = pdf_path.name

    # Get endpoint from env if not provided
    if foundry_project_endpoint is None:
        env_config = dotenv_values()
        foundry_project_endpoint = env_config.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
        if not foundry_project_endpoint:
            raise ValueError(
                "foundry_project_endpoint not provided and "
                "AZURE_FOUNDRY_PROJECT_ENDPOINT not set in environment"
            )

    invalid_modes = set(modes) - set(EXTRACTION_MODES)
    if invalid_modes:
        raise ValueError(
            f"modes must be from {EXTRACTION_MODES}, got invalid: {invalid_modes}"
        )

    schema_hint = json.dumps(make_schema_hint(schema), indent=2)

    # Determine which pages to render for vision mode.
    # Priority: explicit page_hint > auto-detect from text search > all pages (capped).
    vision_pages: list[int] | None = None
    if page_hint:
        parsed = []
        for token in page_hint.split(","):
            token = token.strip()
            if token.isdigit():
                parsed.append(int(token) - 1)  # convert to 0-based
        if parsed:
            vision_pages = parsed
    elif "vision" in modes:
        detected = find_relevant_pages([pdf_bytes], prompt, schema)
        if detected:
            print(
                f"  Auto-detected {len(detected)} relevant page(s) for vision: "
                f"{[p + 1 for p in detected]}",
                file=sys.stderr,
            )
            vision_pages = detected
        # If no pages detected, vision_pages stays None → all pages with safety cap below.

    # Build input content from selected modes
    content: list[dict] = []

    if "file" in modes:
        b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        content.append(
            {
                "type": "input_file",
                "filename": pdf_filename,
                "file_data": f"data:application/pdf;base64,{b64}",
            }
        )

    if "vision" in modes:
        all_images: list[str] = []
        all_images.extend(pdf_to_images(pdf_bytes, pages=vision_pages))

        _MAX_IMAGES = 50
        if len(all_images) > _MAX_IMAGES:
            print(
                f"  WARNING: {len(all_images)} pages would exceed the API limit of "
                f"{_MAX_IMAGES} images. Falling back to first {_MAX_IMAGES} pages.\n"
                "  The auto-detection found no keyword matches — consider refining "
                "the prompt or schema to include more specific section headings.",
                file=sys.stderr,
            )
            all_images = all_images[:_MAX_IMAGES]

        for b64_img in all_images:
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{b64_img}",
                }
            )

    if "text" in modes:
        text_content = pdf_to_text(pdf_bytes)
        content.append(
            {"type": "input_text", "text": f"DOCUMENT TEXT:\n{text_content}"}
        )

    # Assemble extraction instructions
    parts: list[str] = []

    # For table extractions, add preamble instructions
    if isinstance(schema, list):
        parts.append(
            "You are a precise document table extractor. "
            "Return ONLY valid JSON — no markdown, no explanation. "
            "Include ONLY rows that are actual data entries with values in ALL "
            "requested columns. Skip section headers, category headings, "
            "sub-headings, summary rows, and rows where most columns are empty."
        )

    if page_hint:
        parts.append(f"Focus specifically on page(s): {page_hint}")

    parts.append(prompt)

    # Extra guidance for dict (key-value) extractions with code-like fields
    if isinstance(schema, dict):
        dict_notes: list[str] = [
            "IMPORTANT FIELD EXTRACTION NOTES:",
            "- For indicator or flag fields (Y/N, short codes), extract the "
            "indicator value or checkbox state — NOT surrounding descriptive "
            "labels or score category names.",
            "- A 'Flag' field with value 'Y' means the case is flagged "
            "(look for a flagged/yes indicator next to the label, not the "
            "flag colour or score type).",
            "- When extracting from structured sections, extract values at the "
            "specific field level — NOT broad section titles.",
        ]
        field_hints = _collect_dict_field_hints(schema)
        if field_hints:
            hint_lines = "\n".join(
                f"  - '{k}': value is a short code/indicator, e.g. {v!r}"
                for k, v in field_hints.items()
            )
            dict_notes.append(f"Short-code field hints:\n{hint_lines}")
        parts.append("\n".join(dict_notes))

        # For sectioned dict schemas whose values are lists
        # (e.g. ENVIRONMENT/SOCIAL), add completeness instructions and
        # nested column-value hints.
        list_section_keys = [k for k, v in schema.items() if isinstance(v, list)]
        if list_section_keys:
            section_struct_lines: list[str] = []
            for sec_key, sec_val in schema.items():
                if not isinstance(sec_val, list) or not sec_val:
                    continue
                id_vals: list[str] = []
                for item in sec_val:
                    if isinstance(item, dict):
                        for _, fv in item.items():
                            if isinstance(fv, str):
                                id_vals.append(repr(fv))
                                break
                if id_vals:
                    section_struct_lines.append(
                        f"  - {sec_key}: {', '.join(id_vals)}"
                    )
            if section_struct_lines:
                parts.append(
                    "SECTION HEADINGS — navigate to EACH of these in the document "
                    "and extract its data verbatim as the KEY ISSUE value:\n"
                    + "\n".join(section_struct_lines)
                )

            parts.append(
                "EXTRACTION COMPLETENESS — CRITICAL:\n"
                f"The output JSON sections are: {list_section_keys}.\n"
                "The schema below shows EXACTLY the number of placeholder slots "
                "that must be filled — one slot per item.\n"
                "You MUST extract EVERY item from the document to match each slot. "
                "Do NOT stop after the first item.\n"
                "Return [] only if a section is genuinely absent from the document."
            )

            # Collect categorical column-value hints from nested list structures
            nested_hint_lines: list[str] = []
            for section_val in schema.values():
                if not isinstance(section_val, list):
                    continue
                for item in section_val:
                    if not isinstance(item, dict):
                        continue
                    for sub_val in item.values():
                        if not isinstance(sub_val, list) or not sub_val:
                            continue
                        if not isinstance(sub_val[0], dict):
                            continue
                        all_cols: set[str] = set()
                        for row in sub_val:
                            if isinstance(row, dict):
                                all_cols.update(row.keys())
                        for col in sorted(all_cols):
                            seen_vals: dict[str, None] = {}
                            for row in sub_val:
                                if isinstance(row, dict):
                                    v = str(row.get(col, ""))
                                    if v:
                                        seen_vals[v] = None
                            uvals = list(seen_vals.keys())
                            if 0 < len(uvals) <= 6:
                                nested_hint_lines.append(
                                    f"  - '{col}': exact values — "
                                    + ", ".join(repr(x) for x in uvals)
                                )
            if nested_hint_lines:
                deduped = list(dict.fromkeys(nested_hint_lines))
                parts.append(
                    "NESTED FIELD VALUE HINTS (use verbatim):\n"
                    + "\n".join(deduped)
                )

            parts.append(
                "STRUCTURED REPORT FIELD NOTES:\n"
                "- Do NOT prepend category or section header names to "
                "individual field values.\n"
                "- 'Role' field: use the SHORT form 'Direct' (NOT "
                "'Direct Involvement'); keep 'Indirect Involvement' in full.\n"
                "- Score LABEL columns (e.g. 'Practices Score', 'Practice "
                "Score'): return the TEXT LABEL (TOP, MID, LOW, or empty "
                "string '') — NOT any numeric value shown nearby.\n"
                "- 'Flag' field: use 'Y' if flagged, '0' (digit zero) if "
                "NOT flagged — do NOT use the letter 'O' for zero."
            )

    # Extra guidance for table (list) extractions
    if isinstance(schema, list) and schema:
        columns = list(schema[0].keys())
        col_hints: list[str] = []
        for col in columns:
            seen: dict[str, None] = {}
            for row in schema:
                val = str(row.get(col, ""))
                seen[val] = None
            unique_vals = list(seen.keys())
            if len(unique_vals) <= 5:
                col_hints.append(
                    f"  - '{col}' cell values (read verbatim): "
                    f"{', '.join(unique_vals)}"
                )
        col_hint_text = ""
        if col_hints:
            col_hint_text = (
                "\nColumn value format hints:\n" + "\n".join(col_hints) + "\n"
            )
        parts.append(
            "IMPORTANT TABLE EXTRACTION RULES:\n"
            "- Extract ONLY actual data rows from the table.\n"
            "- Do NOT include section headers, category labels, sub-headings, "
            "or summary/score lines as rows.\n"
            "- If a row has text spanning across all columns (like a category "
            "name or divider), it is NOT a data row — skip it entirely.\n"
            "- Do NOT prepend category or section names to individual cell "
            "values. Each cell value should be exactly as it appears in its "
            "own table cell.\n"
            "- Every row MUST have a distinct, non-empty value for EVERY column.\n"
            "- For categorical columns, read the EXACT TEXT LABEL from the "
            "cell; do NOT substitute a related numeric score or visual "
            "indicator — even if the same row also shows a number nearby.\n"
            f"- The table columns are exactly: {columns}\n"
            f"{col_hint_text}"
            "- Combine multi-line cell content into a single string.\n"
            "- Do not nest rows or add extra keys."
        )

    parts.append(
        f"Return ONLY valid JSON matching this structure, no other text:\n"
        f"{schema_hint}"
    )
    content.append({"type": "input_text", "text": "\n\n".join(parts)})

    with (
        AzureCliCredential() as credential,
        AIProjectClient(
            endpoint=foundry_project_endpoint, credential=credential
        ) as project_client,
    ):
        client = project_client.get_openai_client()
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                response = client.responses.create(
                    model=model,
                    input=[{"role": "user", "content": content}],
                )
                return json.loads(response.output_text)
            except Exception as exc:
                is_bad_request = "400" in str(exc) or "BadRequestError" in type(exc).__name__
                if is_bad_request:
                    raise  # non-recoverable; do not retry
                is_rate_limit = (
                    "429" in str(exc)
                    or "too_many_requests" in str(exc).lower()
                    or "rate_limit" in str(exc).lower()
                )
                if is_rate_limit and attempt < max_attempts - 1:
                    wait = 30 * (2 ** attempt)  # 30s, 60s, 120s, 240s
                    print(
                        f"  Rate limited (attempt {attempt + 1}/{max_attempts}), "
                        f"waiting {wait}s …",
                        flush=True,
                    )
                    _time.sleep(wait)
                else:
                    raise
        # Unreachable — loop always returns or raises
        raise RuntimeError("Extraction failed after all retry attempts")


def _load_schema(schema_name: str | None) -> dict | list:
    """Load a schema JSON file as a data template for extraction.

    Returns the schema as-is.  If the file is a JSON Schema definition
    (contains ``$schema`` or ``$defs``), it is still returned — the model
    can use the structure to understand the expected output shape.
    """
    if not schema_name:
        return {}
    schema_path = _SCHEMA_DIR / f"{schema_name}"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    with open(schema_path) as f:
        schema = json.load(f)

    return schema


def extract(
    pdf_path: str | Path,
    prompt: str,
    foundry_project_endpoint: str | None = None,
    model: str = "gpt-5.1",
    schema_name: str | None = None,
) -> dict:
    """Extract structured data from a PDF file.

    Matches the ``ExtractorFn`` protocol used by ``esg.evaluate``.
    Loads the schema from ``schemas/<schema_name>_schema.json`` when
    *schema_name* is provided.
    """
    schema = _load_schema(schema_name)
    result = _extract_with_schema(
        pdf_path=pdf_path,
        prompt=prompt,
        schema=schema,
        foundry_project_endpoint=foundry_project_endpoint,
        model=model,
    )
    return result if isinstance(result, dict) else {"data": result}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract structured data from PDFs"
    )
    parser.add_argument(
        "--pdf",
        required=True,
        help="PDF file to extract from",
    )
    parser.add_argument(
        "--prompt",
        required=True,
        help="Extraction instruction prompt",
    )
    parser.add_argument(
        "--schema-file",
        required=True,
        help="JSON file with expected output structure",
    )
    parser.add_argument(
        "--output",
        default="output/result.json",
        help="Output JSON file path (default: output/result.json)",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.1",
        help="Model deployment name (default: gpt-5.1)",
    )
    parser.add_argument(
        "--mode",
        nargs="+",
        default=list(EXTRACTION_MODES),
        choices=EXTRACTION_MODES,
        help=(
            "Input modes for PDF content (default: all three). "
            "Multiple modes can be combined for better accuracy."
        ),
    )
    parser.add_argument(
        "--page-hint",
        help="Optional page numbers to focus on (e.g. '3,4')",
    )
    args = parser.parse_args()

    schema_path = Path(args.schema_file)
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    with open(schema_path) as f:
        schema = json.load(f)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    result = _extract_with_schema(
        pdf_path=pdf_path,
        prompt=args.prompt,
        schema=schema,
        foundry_project_endpoint=None,
        model=args.model,
        modes=tuple(args.mode),
        page_hint=args.page_hint,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Saved: {output_path}", file=sys.stderr)
    print(json.dumps(result, indent=2))
