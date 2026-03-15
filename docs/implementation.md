# Implementation: PDF Structured Data Extractor

## Overview

This document describes the design, architecture, and implementation of the ESG
PDF Structured Data Extractor — a Python service that uses Azure AI Foundry to
extract structured fields from PDF documents. The service is **decoupled from
ground truth files** and can be called from a CLI, a test suite, or any other
caller that can supply raw bytes.

---

## Problem Statement

ESG rating documents (and similar financial/regulatory reports) are visually
rich PDFs: they contain scorecards, gauge charts, and multi-column tables that
do not linearise cleanly when converted to plain text. Existing text-extraction
approaches produce out-of-order, ambiguous strings that are unreliable for
structured field extraction.

The goal is a repeatable, accurate pipeline that:

1. Accepts any PDF and a natural-language prompt describing which fields to
   extract.
2. Accepts a JSON schema that specifies the **shape** of the expected output
   (not the values themselves).
3. Returns a JSON object matching that shape.
4. Optionally evaluates the result against ground truth (in the test suite).

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     CLI / Python API                     │
│            src/esg/run_extraction_custom.py              │
└──────────────┬──────────────────────┬───────────────────┘
               │                      │
   ┌───────────▼──────────┐  ┌────────▼────────────┐
   │    PDF Ingestion      │  │   Schema / Prompt    │
   │  (bytes — no paths)  │  │   Builder            │
   └───────────┬──────────┘  └────────┬────────────┘
               │                      │
   ┌───────────▼──────────────────────▼────────────┐
   │        Input Mode Combiner                     │
   │  file (native PDF)  +  vision (PNG images)    │
   │                     +  text (pymupdf plain)   │
   └──────────────────────┬────────────────────────┘
                          │
   ┌──────────────────────▼────────────────────────┐
   │        Azure AI Foundry — Responses API        │
   │   AIProjectClient → client.responses.create    │
   └──────────────────────┬────────────────────────┘
                          │
   ┌──────────────────────▼────────────────────────┐
   │   json.loads → typed dict | list              │
   └──────────────────────┬────────────────────────┘
                          │
        ┌─────────────────▼──────────────────┐
        │       Evaluator                     │
        │   extracted vs ground_truth         │
        │   field-level match + % accuracy    │
        └─────────────────┬──────────────────┘
                          │
              ┌───────────┼───────────┐
              ▼                       ▼
   extracted_output.json      evaluation_report.json
```

---

## Key Design Decision: Multi-Mode Input

Three input representations of the same PDF are sent to the model together,
giving it complementary views to cross-reference:

| Mode | How | Best for |
|---|---|---|
| Vision (default) | 100% | Charts, tables, visual scorecards |
| Text (`--text-only`) | ~0% for visual PDFs | Dense prose / text-only documents |

---

## Implementation Details

### Module: `src/pdf_structured_extractor.py`

| Function | Responsibility |
|---|---|
| `load_pdf(path)` | Extracts plain text from all pages using PyMuPDF — used by `--text-only` mode |
| `load_pdf_as_images(path, dpi=200)` | Renders each page as a base64 PNG at 200 DPI — default pipeline |
| `load_ground_truth_json(path)` | Loads & validates the JSON schema; enforces required keys and type of `ground_truth` |
| `build_extraction_prompt(schema, *, page_images, pdf_text)` | Builds `(system_message, user_content)` — user content contains image parts + instruction text (vision) or raw text (fallback) |
| `call_azure_openai(system, content, *, foundry_project_endpoint)` | Resolves the first available deployment via `AIProjectClient`, calls `chat.completions.create` with `response_format=json_object` and `temperature=0` |
| `parse_model_response(text)` | `json.loads` with a clear error if the model returns non-JSON |
| `compare_with_ground_truth(extracted, ground_truth)` | Walks nested `ground_truth`, compares string-normalised values, aggregates accuracy |
| `main(pdf, json, output_dir, *, text_only)` | Orchestrates the full pipeline; writes `extracted_output.json` and `evaluation_report.json` |

### Ground Truth JSON Schema

```json
{
  "id":     "string — unique identifier for the extraction task",
  "query":  "string — human-readable description of the target section",
  "prompt": "string — natural-language instruction for the model",
  "context":"string — section heading to focus on",
  "ground_truth": {
    "<FIELD>": {
      "<SUB_FIELD>": "<expected value>"
    }
  }
}
```

The schema drives both the extraction prompt (only the fields listed in
`ground_truth` are requested) and the evaluation (every leaf value is compared
exactly, after whitespace normalisation).

### Authentication

The tool uses `AzureCliCredential` — no API keys are embedded anywhere.
Users authenticate once with `az login` and the SDK picks up the token
automatically. The Foundry project endpoint is the only required secret and
lives in `.env` (git-ignored).

### Output Files

| File | Contents |
|---|---|
| `extracted_output.json` | The structured JSON extracted by the model, shaped to match `ground_truth` |
| `evaluation_report.json` | `field_results` (per-field match/mismatch) and `summary` (total, matches, mismatches, accuracy %) |

---

## Prompting Strategy

The system prompt establishes the model's role and output contract:

> "You are a precise document data extraction assistant. You extract structured
> information from documents and return ONLY valid JSON. Do not include any
> explanation, markdown formatting, or text outside the JSON object. If a value
> cannot be found, use null."

The user message contains:

1. `DOCUMENT SECTION TO FOCUS ON:` — scopes the model to the right region.
2. `QUERY:` — provides intent.
3. `INSTRUCTION:` — the natural-language prompt from the schema.
4. A templated JSON skeleton with `<value>` placeholders — forces the model to
   return exactly the requested fields and nothing else.
5. The page image(s) or document text.

`response_format={"type": "json_object"}` is set at the API level to guarantee
valid JSON even if the model is tempted to add prose.

---

## Project Structure

```
foundry-hack-1/
├── src/
│   └── pdf_structured_extractor.py   # main module
├── tests/
│   └── test_pdf_structured_extractor.py  # 19 unit tests (pure logic)
├── ESG-files/
│   ├── data/
│   │   └── BASF_Key_issue_assessment.pdf
│   └── ground_truth/
│       └── BASF_Key_issue_assessment.json
├── output/                           # git-ignored; written at runtime
│   ├── extracted_output.json
│   └── evaluation_report.json
├── pyproject.toml                    # all deps + pytest config
├── .env                              # git-ignored; secrets
├── .env.example
└── docs/
    ├── plan.md
    └── implementation.md         # this file
```

---

## Running the Extraction Script

```bash
# Install dependencies (includes dev deps for pytest)
uv sync --group dev

# Run extraction (vision mode, default)
uv run python src/pdf_structured_extractor.py \
  --pdf ESG-files/data/BASF_Key_issue_assessment.pdf \
  --json ESG-files/ground_truth/BASF_Key_issue_assessment.json \
  --output-dir output

# Run extraction (text-only fallback)
uv run python src/pdf_structured_extractor.py \
  --pdf ESG-files/data/BASF_Key_issue_assessment.pdf \
  --json ESG-files/ground_truth/BASF_Key_issue_assessment.json \
  --output-dir output \
  --text-only

# Run tests
uv run pytest tests/ -v
```

---

## Test Coverage

### Unit tests (`tests/esg/test_pdf_structured_extractor.py`)

| Test class | What is tested |
|---|---|
| `TestLoadGroundTruthJson` | Valid load, missing file, missing required keys, non-dict `ground_truth` |
| `TestBuildExtractionPrompt` | Text mode content, vision mode (1 and N pages), missing-input error, placeholder structure, instruction text |
| `TestParseModelResponse` | Valid JSON, invalid JSON, empty string |
| `TestCompareWithGroundTruth` | 100% match, partial match, all-missing, field detail shape, flat schema, whitespace normalisation |

```
19 passed in ~1s
```

---

## Extending the Pipeline

**Add a new scenario** — drop the PDF into `ESG-files/data/`, add a
corresponding ground truth JSON + schema into `ESG-files/ground_truth/ScenarioN/`,
then add a `TestScenarioN` class to `tests/esg/test_integration.py` following
the existing pattern.

**Batch evaluation** — wrap `main()` in a loop over all JSON files in
`ESG-files/ground_truth/` to produce aggregated accuracy across all schemas.

**Different model** — `call_azure_openai` picks the first deployment from the
Foundry project. To target a specific deployment, pass its name explicitly by
extending the function signature with an optional `model_name` parameter.
