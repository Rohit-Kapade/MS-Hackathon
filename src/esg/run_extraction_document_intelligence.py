"""Document Intelligence extraction runner.

Uses Azure Document Intelligence to convert PDFs to markdown, then sends
the markdown to an LLM via Azure Foundry for structured data extraction.

Usage::

    uv run python src/esg/run_extraction_document_intelligence.py \\
        --pdf ESG-files/data/Cargill_Controversies_Environment.pdf \\
        --prompt "Extract all environment controversies with case details" \\
        --schema controversies_case_assessment_schema.json \\
        --model gpt-5.1
"""

import argparse
import json
import re
import sys
from pathlib import Path

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential, DefaultAzureCredential
from dotenv import dotenv_values

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "ESG-files" / "schemas"

def load_schema(name: str) -> str:
    """Load a JSON schema by filename from the schemas directory."""
    path = _SCHEMA_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Schema file not found: {path}"
        )
    return path.read_text()


# ---------------------------------------------------------------------------
# Document Intelligence: PDF → markdown
# ---------------------------------------------------------------------------


class DocumentIntelligenceExtractor:
    """Extracts content from PDF files using Azure Document Intelligence,
    returning the full document as a single markdown string."""

    def __init__(self, endpoint: str | None = None, model: str = "prebuilt-layout"):
        config = dotenv_values()
        self.endpoint = endpoint or config.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")

        if not self.endpoint:
            raise ValueError(
                "Azure Document Intelligence endpoint not found. "
                "Please set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT in .env "
                "or pass it as a parameter."
            )

        self.model = model
        credential = DefaultAzureCredential()
        self.client = DocumentIntelligenceClient(endpoint=self.endpoint, credential=credential)

    def extract_from_pdf(self, pdf_path: str | Path) -> str:
        pdf_file_path = Path(pdf_path)
        if not pdf_file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_file_path}")

        with open(pdf_file_path, "rb") as f:
            poller = self.client.begin_analyze_document(
                model_id=self.model,
                body=f,
                content_type="application/pdf",
                output_content_format="markdown",
                features=["styleFont"],
            )

        result = poller.result()
        content = result.content
        return _annotate_highlighted_scores(content, result.styles)


# ---------------------------------------------------------------------------
# Style annotation: mark highlighted LOW/MID/TOP scores in the markdown
# ---------------------------------------------------------------------------

# Maximum character gap between consecutive score labels to be considered
# part of the same group (i.e. the same table row).
_GROUP_GAP = 80


def _hex_brightness(hex_color: str) -> float:
    """Return average brightness (0-255) of a hex colour string."""
    h = hex_color.lstrip("#")
    if len(h) < 6:
        return 255.0
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r + g + b) / 3.0


def _annotate_highlighted_scores(content: str, styles: list | None) -> str:
    """Find score labels (LOW, MID, TOP) that are visually selected in the
    PDF and wrap them with ``[SELECTED: ...]`` in the markdown.

    The detection works by comparing the **background colour darkness**
    of nearby score labels.  In MSCI ESG reports the selected score has a
    coloured background (teal for TOP, red for LOW, grey for MID) while
    unselected labels sit on a light beige background.  Within each group
    of co-located labels, the one with the darkest background is selected.
    """
    if not styles:
        return content

    # 1. Build a map: character offset → background colour hex.
    bg_map: dict[int, str] = {}
    for style in styles:
        bg = getattr(style, "background_color", None)
        if not bg:
            continue
        for span in getattr(style, "spans", []):
            for i in range(span.offset, span.offset + span.length):
                bg_map[i] = bg

    if not bg_map:
        return content

    # 2. Locate every LOW / MID / TOP token.
    matches = list(re.finditer(r"\b(?:LOW|MID|TOP)\b", content))
    if not matches:
        return content

    # 3. Group tokens that are close together (same table row).
    groups: list[list[re.Match]] = []  # type: ignore[type-arg]
    cur: list[re.Match] = []  # type: ignore[type-arg]
    for m in matches:
        if cur and m.start() - cur[-1].end() > _GROUP_GAP:
            if len(cur) >= 2:
                groups.append(cur)
            cur = [m]
        else:
            cur.append(m)
    if len(cur) >= 2:
        groups.append(cur)

    # 4. In each group, find the label with the darkest background.
    selected_spans: set[tuple[int, int]] = set()
    for group in groups:
        best_match: re.Match | None = None  # type: ignore[type-arg]
        best_brightness = 256.0
        for m in group:
            brightnesses = [
                _hex_brightness(bg_map[i])
                for i in range(m.start(), m.end())
                if i in bg_map
            ]
            if not brightnesses:
                continue
            avg = sum(brightnesses) / len(brightnesses)
            if avg < best_brightness:
                best_brightness = avg
                best_match = m

        # Only annotate if the darkest bg is meaningfully darker than
        # the lightest in the group.  Compare against the group's
        # lightest background rather than using a fixed threshold so
        # that grey "MID" highlights (which sit around brightness 180)
        # are still detected even when beige backgrounds are ~243.
        if best_match is not None and best_brightness < 220:
            # Additionally check relative contrast: darkest must be
            # noticeably darker than the lightest in the group.
            group_brightnesses = []
            for m in group:
                for i in range(m.start(), m.end()):
                    if i in bg_map:
                        group_brightnesses.append(_hex_brightness(bg_map[i]))
            lightest = max(group_brightnesses) if group_brightnesses else 255.0
            if lightest - best_brightness > 25:
                selected_spans.add((best_match.start(), best_match.end()))

    if not selected_spans:
        return content

    # 5. Replace selected labels in the text.
    def _maybe_annotate(match: re.Match) -> str:  # type: ignore[type-arg]
        if (match.start(), match.end()) in selected_spans:
            return f"[SELECTED: {match.group()}]"
        return match.group()

    return re.sub(r"\b(?:LOW|MID|TOP)\b", _maybe_annotate, content)


# ---------------------------------------------------------------------------
# LLM extraction: markdown → structured JSON
# ---------------------------------------------------------------------------


def extract_with_prompt(
    markdown: str,
    prompt: str,
    *,
    foundry_project_endpoint: str,
    model: str = "gpt-5.1",
    schema_name: str,
) -> dict:
    """Extract structured data from markdown using the given prompt."""
    schema_text = load_schema(schema_name)
    text = (
        f"{prompt}\n\n"
        "Return ONLY valid JSON matching this schema, no markdown fencing or explanation.\n\n"
        f"JSON Schema:\n{schema_text}\n\n"
        f"Document content:\n{markdown}"
    )

    with (
        AzureCliCredential() as credential,
        AIProjectClient(
            endpoint=foundry_project_endpoint, credential=credential
        ) as project_client,
    ):
        client = project_client.get_openai_client()
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": text},
                    ],
                }
            ],
        )
        raw = response.output_text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]
        return json.loads(raw)


# ---------------------------------------------------------------------------
# ExtractorFn-compatible wrapper for the evaluate framework
# ---------------------------------------------------------------------------

import re


# ---------------------------------------------------------------------------
# Post-processing: clean up common OCR artifacts in extracted values
# ---------------------------------------------------------------------------


def _clean_ocr_value(value: str) -> str:
    """Fix common OCR artifacts in a single string value."""
    # Rejoin words broken by line breaks (e.g. "a base\nlow" → "a base low")
    s = value.replace("\r\n", " ").replace("\n", " ")
    # Fix hyphen-broken words  (e.g. "performance- based" → "performance-based")
    s = re.sub(r"(\w)- (\w)", r"\1-\2", s)
    # Collapse multiple spaces
    s = re.sub(r" {2,}", " ", s)
    return s.strip()


def _clean_ocr_recursive(obj: object) -> object:
    """Recursively clean OCR artifacts in all string leaves."""
    if isinstance(obj, dict):
        return {k: _clean_ocr_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_ocr_recursive(v) for v in obj]
    if isinstance(obj, str):
        return _clean_ocr_value(obj)
    return obj


# ---------------------------------------------------------------------------
# Content-filter-safe chunking. Azure's jailbreak filter flags very large
# markdown payloads as false positives, so we split the document across
# multiple user messages.
# ---------------------------------------------------------------------------
_CHUNK_SIZE = 80_000


def _chunk_text(text: str, chunk_size: int = _CHUNK_SIZE) -> list[str]:
    """Split text into chunks, breaking at line boundaries."""
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            # Try to break at a newline
            nl = text.rfind("\n", start, end)
            if nl > start:
                end = nl + 1
        chunks.append(text[start:end])
        start = end
    return chunks


def extract(
    pdf_path: Path,
    prompt: str,
    *,
    foundry_project_endpoint: str,
    model: str = "gpt-5.1",
    schema_name: str,
) -> dict:
    """Extract structured data from a PDF via Document Intelligence + LLM.

    Matches the ``ExtractorFn`` protocol used by ``esg.evaluate``.
    """
    di_extractor = DocumentIntelligenceExtractor()
    markdown = di_extractor.extract_from_pdf(pdf_path)

    # Persist intermediate markdown for debugging
    md_output_dir = Path(__file__).resolve().parents[2] / "output" / "esg" / "document_intelligence" / "markdown_cache"
    md_output_dir.mkdir(parents=True, exist_ok=True)
    md_path = md_output_dir / pdf_path.with_suffix(".md").name
    md_path.write_text(markdown)

    # Build the instruction text with the schema
    schema_text = load_schema(schema_name)
    instruction = (
        f"{prompt}\n\n"
        "Return ONLY valid JSON matching this schema "
        "(no markdown fencing or explanation).\n\n"
        f"JSON Schema:\n{schema_text}"
    )

    # Split document into chunks to avoid content-filter false positives
    chunks = _chunk_text(markdown)

    # Build user content: document chunks + instruction
    user_content: list[dict] = []
    for i, chunk in enumerate(chunks):
        label = (
            "--- BEGIN DOCUMENT ---"
            if len(chunks) == 1
            else f"--- DOCUMENT PART {i + 1}/{len(chunks)} ---"
        )
        end_label = "--- END DOCUMENT ---" if i == len(chunks) - 1 else ""
        user_content.append({
            "type": "input_text",
            "text": f"{label}\n{chunk}\n{end_label}".strip(),
        })
    user_content.append({"type": "input_text", "text": instruction})

    input_messages: list = [
        {
            "type": "message",
            "role": "developer",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "You are a structured-data extraction assistant. "
                        "The user will provide a document converted from PDF via OCR and a request. "
                        "Always respond with valid JSON only.\n\n"
                        "OCR extraction guidelines:\n"
                        "- The document text comes from OCR and may contain artifacts: "
                        "line breaks in the middle of cell values, misaligned columns, "
                        "or missing characters. Reconstruct the intended text.\n"
                        "- For table extraction: extract EVERY row, including rows "
                        "where cell values are numbers, percentages, or short codes. "
                        "Do not skip any rows.\n"
                        "- CRITICAL: Some table rows contain ONLY numeric values "
                        "(like '56.41%', '7.00', '12.13%', '0') as the Company Practice, "
                        "with empty Best Practice and empty Practices Score. "
                        "These ARE valid table rows — you MUST extract them. "
                        "Do NOT skip any row even if it looks like a standalone metric "
                        "rather than a table entry. Use an empty string for blank fields.\n"
                        "- Score/rating columns (e.g. 'Practice Score', 'Practices Score') "
                        "use LOW, MID, or TOP. When a score label is wrapped as "
                        "[SELECTED: X] (e.g. [SELECTED: TOP]), that is the active/chosen "
                        "score for that row — use 'X' as the Practices Score value. "
                        "If no label is marked [SELECTED:], use an empty string.\n"
                        "- When extracting 'Role' fields, use the full form (e.g. "
                        "'Direct Involvement' not just 'Direct').\n"
                        "- When a table continues on the next page without repeating "
                        "column headers, still extract those continuation rows.\n"
                        "- Cell value alignment: when a single long cell value appears "
                        "split across two columns in the OCR output (e.g. a Company "
                        "Practice value that overflows into the Best Practice column), "
                        "keep it as ONE value in the correct column and leave the "
                        "other column empty. Look at the semantic meaning to decide."
                    ),
                },
            ],
        },
        {
            "type": "message",
            "role": "user",
            "content": user_content,
        },
    ]

    with (
        AzureCliCredential() as credential,
        AIProjectClient(
            endpoint=foundry_project_endpoint, credential=credential
        ) as project_client,
    ):
        client = project_client.get_openai_client()
        response = client.responses.create(
            model=model,
            input=input_messages,
            text={"format": {"type": "json_object"}},
        )
        raw = response.output_text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]
        result = json.loads(raw)

        # Clean up OCR artifacts in all string values
        cleaned = _clean_ocr_recursive(result)
        return cleaned if isinstance(cleaned, dict) else {"data": cleaned}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run(
    pdf_path: Path,
    prompt: str,
    output_dir: Path,
    *,
    foundry_project_endpoint: str,
    schema_name: str,
    model: str = "gpt-5.1",
    di_model: str = "prebuilt-layout",
) -> dict:
    """Run Document Intelligence extraction on a single PDF and return results."""
    output_dir.mkdir(parents=True, exist_ok=True)
    di_extractor = DocumentIntelligenceExtractor(model=di_model)

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"PDF:          {pdf_path.name}", file=sys.stderr)
    print(f"Schema:       {schema_name}", file=sys.stderr)
    print(f"Model:        {model}", file=sys.stderr)
    print(f"DI Model:     {di_model}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    # Step 1: Convert PDF to markdown via Document Intelligence
    print(f"Extracting markdown from {pdf_path.name} ...", file=sys.stderr)
    md = di_extractor.extract_from_pdf(pdf_path)

    output_md = output_dir / pdf_path.with_suffix(".md").name
    output_md.write_text(md)
    print(f"  Saved: {output_md}", file=sys.stderr)

    # Step 2: Extract structured data using LLM
    extracted = extract_with_prompt(
        md,
        prompt,
        foundry_project_endpoint=foundry_project_endpoint,
        model=model,
        schema_name=schema_name,
    )

    extracted_path = output_dir / f"{pdf_path.stem}.extracted.json"
    with open(extracted_path, "w") as f:
        json.dump(extracted, f, indent=2)
    print(f"  Saved: {extracted_path}", file=sys.stderr)

    return extracted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract structured data from PDFs using Document Intelligence + LLM"
    )
    parser.add_argument(
        "--pdf",
        required=True,
        help="PDF file to extract from",
    )
    parser.add_argument(
        "--prompt",
        required=True,
        help="Extraction prompt to send to the LLM",
    )
    parser.add_argument(
        "--schema",
        required=True,
        help="Schema filename in ESG-files/schemas/ (e.g. controversies_case_assessment_schema.json)",
    )
    parser.add_argument(
        "--output-dir",
        default="ESG-files/di_extracted",
        help="Directory for output files (default: ESG-files/di_extracted)",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.1",
        help="Model deployment name (default: gpt-5.1)",
    )
    parser.add_argument(
        "--di-model",
        default="prebuilt-layout",
        help="Document Intelligence model (default: prebuilt-layout)",
    )
    args = parser.parse_args()

    config = dotenv_values()
    foundry_endpoint = config.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
    if not foundry_endpoint:
        raise RuntimeError("AZURE_FOUNDRY_PROJECT_ENDPOINT not set in .env")

    results = run(
        pdf_path=Path(args.pdf),
        prompt=args.prompt,
        output_dir=Path(args.output_dir),
        foundry_project_endpoint=foundry_endpoint,
        schema_name=args.schema,
        model=args.model,
        di_model=args.di_model,
    )

    # Print results to stdout
    print(json.dumps(results, indent=2))
