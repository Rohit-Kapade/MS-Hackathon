"""Simple single-file extraction sending a PDF and prompt to a Foundry model.

Usage::

    uv run python src/esg/run_extraction_raw.py \
        --pdf ESG-files/data/BASF_Rating_Carbon_Emission.pdf \
        --prompt "Extract all carbon emission metrics …" \
        --output-dir output \
        --model gpt-5.1
"""

import argparse
import base64
import json
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "ESG-files" / "schemas"


def extract(
    pdf_path: Path,
    prompt: str,
    foundry_project_endpoint: str,
    schema_name: str,
    model: str = "gpt-5.1",
) -> dict:
    """Extract structured data from a PDF using the given prompt."""

    base64_string = base64.b64encode(pdf_path.read_bytes()).decode("utf-8")

    schema_path = _SCHEMA_DIR / schema_name
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    schema_text = schema_path.read_text()
    user_prompt = (
        f"{prompt}\n\n"
        "Return ONLY valid JSON matching this schema, "
        "no markdown fencing or explanation.\n\n"
        f"JSON Schema:\n{schema_text}"
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
            max_output_tokens=16384,
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "filename": pdf_path.name,
                            "file_data": f"data:application/pdf;base64,{base64_string}",
                        },
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        },
                    ],
                },
            ],
            text={"format": {"type": "json_object"}},
        )

        return json.loads(response.output_text.strip())


if __name__ == "__main__":
    from dotenv import dotenv_values

    parser = argparse.ArgumentParser(
        description="Extract structured data from a PDF using a prompt"
    )
    parser.add_argument(
        "--pdf", required=True, help="Path to the PDF file to extract from"
    )
    parser.add_argument(
        "--prompt",
        required=True,
        help="Extraction prompt to send alongside the PDF",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for output files (default: output)",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.1",
        help="Model deployment name (default: gpt-5.1)",
    )
    args = parser.parse_args()

    config = dotenv_values()
    foundry_endpoint = config.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
    if not foundry_endpoint:
        raise RuntimeError("AZURE_FOUNDRY_PROJECT_ENDPOINT not set in .env")

    pdf_path = Path(args.pdf)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"PDF:   {pdf_path}", file=sys.stderr)
    print(f"Model: {args.model}", file=sys.stderr)

    result = extract(
        pdf_path=pdf_path,
        prompt=args.prompt,
        foundry_project_endpoint=foundry_endpoint,
        schema_name=args.schema_name,
        model=args.model,
    )

    out_file = output_dir / f"{pdf_path.stem}_extracted.json"
    out_file.write_text(json.dumps(result, indent=2))
    print(f"Saved: {out_file}", file=sys.stderr)

    print(json.dumps(result, indent=2))
