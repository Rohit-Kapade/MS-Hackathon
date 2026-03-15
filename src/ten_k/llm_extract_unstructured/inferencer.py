"""LLM-based inferencer (unstructured): PDF → Document Intelligence → LLM extraction.

Extracts financial line items by sending PDFs through Azure Document
Intelligence for markdown extraction, then prompting an LLM to return raw
JSON (no structured-output API). This is designed for models that do
not support structured outputs, such as Phi-4.

Usage:
    uv run python -m ten_k.llm_extract_unstructured.inferencer data/ --year 2023
    uv run python -m ten_k.llm_extract_unstructured.inferencer data/report.pdf --year 2023 --model Phi-4
"""

from __future__ import annotations

import getpass
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
from dotenv import dotenv_values
from openai import RateLimitError
from pydantic import ValidationError

from models import (
    Model,
    UnstructuredModel,
    SCORING_ENDPOINTS,
    SCORING_API_KEY_ENV_VARS,
    SCORING_MODELS,
)
from ten_k.extract_tables import process_pdf_markdown
from ten_k.extraction import Extraction

# All field names from the Extraction model, used to build the JSON schema
# description embedded directly in the prompt.
_FIELD_DESCRIPTIONS: dict[str, str] = {
    "revenue": "Total revenue, net sales, or net revenue from the income statement.",
    "cogs": "Cost of goods sold, cost of sales, or cost of revenue.",
    "gross_profit": "Gross profit (revenue minus COGS).",
    "sga": "Selling, general and administrative expenses.",
    "total_operating_expenses": "Total operating expenses.",
    "taxes": "Income tax expense or provision for income taxes.",
    "interest_expense": "Interest expense (net of interest income if reported together).",
    "interest_income": "Interest income, if separately disclosed.",
    "da": "Depreciation and amortization expense.",
    "net_income": "Net income or net earnings attributable to common stockholders.",
    "cash_from_operations": "Net cash provided by (used in) operating activities.",
    "change_in_cash": "Net increase (decrease) in cash and cash equivalents.",
    "changes_in_nwc": "Changes in net working capital (aggregate of WC line items).",
    "cash_from_investing": "Net cash provided by (used in) investing activities.",
    "capex": "Capital expenditures (purchases of property, plant & equipment).",
    "acquisitions": "Cash paid for business or asset acquisitions.",
    "divestitures": "Proceeds from divestitures or sale of businesses.",
    "cash_from_financing": "Net cash provided by (used in) financing activities.",
    "exchange_rates_other": "Effect of exchange rate changes on cash, or other adjustments.",
    "cash_interest_net": "Cash interest paid (net), from supplemental cash flow disclosure.",
    "cash_taxes": "Income taxes paid in cash, from supplemental cash flow disclosure.",
    "dividends": "Dividends paid to shareholders.",
    "net_share_issuance": "Net share issuance or repurchase of common stock.",
    "net_debt_issuance": "Net debt issuance or repayment of long-term debt.",
    "cash": "Cash and cash equivalents on the balance sheet.",
    "accounts_receivable": "Accounts receivable or trade receivables (net).",
    "inventory": "Inventories.",
    "current_assets": "Total current assets.",
    "goodwill": "Goodwill.",
    "other_intangibles": "Intangible assets (net), excluding goodwill.",
    "total_assets": "Total assets.",
    "short_term_debt": "Short-term debt or current portion of long-term debt.",
    "accounts_payable": "Accounts payable.",
    "accrued_expenses": "Accrued expenses, accrued liabilities, or other current liabilities.",
    "deferred_revenue": "Deferred revenue or contract liabilities.",
    "current_liabilities": "Total current liabilities.",
    "total_liabilities": "Total liabilities.",
    "shareholders_equity": "Total stockholders' equity (or deficit).",
    "operating_lease_obligations": "Operating lease obligations (right-of-use liabilities).",
}

_SCHEMA_LINES = "\n".join(
    f'    "{k}": <number or null>' for k in _FIELD_DESCRIPTIONS
)

SYSTEM_PROMPT = f"""\
You are a financial data extraction specialist. You will be given the \
markdown content of a 10-K SEC filing as extracted by Document Intelligence.

Your task:
1. Analyse the provided document carefully.
2. For each field listed below, find the matching line item and \
extract the numeric value for the requested fiscal year.
3. Use your financial knowledge to resolve ambiguities — for example, \
"Net revenues" maps to revenue, "Provision for income taxes" maps to taxes, etc.
4. When a value is shown in parentheses like (123.4), it is negative: \
return -123.4.
5. Strip dollar signs, commas, and whitespace from numbers.
6. If a line item is genuinely not present in the document, return null for \
that field.
7. Return every number exactly as it appears in the document after stripping \
formatting. Do NOT convert units — if the document says 288,945 return 288945.

## Required JSON output format

Return ONLY a JSON object with the following keys (no extra text):

{{
{_SCHEMA_LINES}
}}"""


def _build_user_prompt(markdown: str, target_year: str) -> str:
    """Build the user-turn prompt containing DI markdown and target year."""
    return (
        f"## Target fiscal year\n{target_year}\n\n"
        f"## Document content\n{markdown}"
    )


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> dict:
    """Extract a JSON object from model text output.

    Tries a fenced code block first, then falls back to finding the
    first ``{…}`` span in the text.
    """
    # Try fenced code block first
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return json.loads(m.group(1))

    # Fallback: find the outermost { … }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError("No JSON object found in model response")


class LLMUnstructuredInferencer:
    """Extracts financials by prompting an LLM to return raw JSON.

    Unlike :class:`~ten_k.llm_extract.inferencer.LLMInferencer`, this
    does **not** use the structured-output API, making it compatible
    with models that lack native schema enforcement (e.g. Phi-4).
    """

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        force: bool = False,
        model: UnstructuredModel = Model.PHI_4,
    ) -> None:
        self.output_dir = output_dir
        self.force = force
        self.model: Model = Model(model)

    def extract(
        self,
        pdf_path: Path,
        *,
        target_year: str,
    ) -> Extraction:
        """Extract financial data from a single 10-K PDF.

        Runs DI markdown extraction, then prompts the LLM to return JSON
        and parses the result manually.
        """
        out = self.output_dir if self.output_dir is not None else pdf_path.parent

        md_path = process_pdf_markdown(
            pdf_path, output_dir=out, force=self.force
        )
        markdown = md_path.read_text(encoding="utf-8")
        user_prompt = _build_user_prompt(markdown, target_year)
        raw_json = self._call_llm(user_prompt)

        return Extraction(**raw_json)

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _call_llm(self, user_prompt: str) -> dict:
        """Send the prompt to Azure Foundry and parse JSON from the response.

        Retries up to 5 times on 429 rate-limit errors with exponential
        backoff, and up to 2 times on JSON parse / validation failures.

        For scoring-endpoint models (e.g. financial-reports-analysis-v2),
        delegates to :meth:`_call_scoring_api` instead.
        """
        if self.model in SCORING_MODELS:
            return self._call_scoring_api(user_prompt)

        config = dotenv_values()
        endpoint = config.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
        if not endpoint:
            raise RuntimeError(
                "AZURE_FOUNDRY_PROJECT_ENDPOINT not set in .env"
            )

        max_retries = 5
        max_parse_retries = 2

        with (
            AzureCliCredential() as credential,
            AIProjectClient(
                endpoint=endpoint, credential=credential
            ) as project_client,
        ):
            client = project_client.get_openai_client()

            for parse_attempt in range(max_parse_retries + 1):
                for attempt in range(max_retries + 1):
                    try:
                        response = client.responses.create(
                            model=self.model.value,
                            instructions=SYSTEM_PROMPT,
                            input=[
                                {
                                    "role": "user",
                                    "content": user_prompt,
                                },
                            ],
                        )
                        break
                    except RateLimitError:
                        if attempt == max_retries:
                            raise
                        wait = min(15 * 2 ** attempt, 60)
                        print(
                            f"  Rate limited, retrying in {wait}s "
                            f"(attempt {attempt + 1}/{max_retries}) …",
                            file=sys.stderr,
                        )
                        time.sleep(wait)

                raw_text = response.output_text

                try:
                    data = _extract_json(raw_text)
                    # Validate through Extraction to catch bad values early
                    Extraction(**data)
                    return data
                except (json.JSONDecodeError, ValueError, ValidationError) as exc:
                    if parse_attempt == max_parse_retries:
                        raise RuntimeError(
                            f"Failed to parse valid JSON after "
                            f"{max_parse_retries + 1} attempts. "
                            f"Last response:\n{raw_text}"
                        ) from exc
                    print(
                        f"  JSON parse/validation failed ({exc!r}), "
                        f"retrying (attempt {parse_attempt + 1}/{max_parse_retries}) …",
                        file=sys.stderr,
                    )

        # Unreachable, but keeps type checkers happy
        raise RuntimeError("Unexpected exit from retry loop")

    # ------------------------------------------------------------------
    # Scoring API interaction
    # ------------------------------------------------------------------

    def _get_scoring_api_key(self) -> str:
        """Resolve the API key from env var, .env file, or interactive prompt."""
        env_var = SCORING_API_KEY_ENV_VARS[self.model]

        # 1. OS environment variable
        key = os.environ.get(env_var)
        if key:
            return key

        # 2. .env file
        config = dotenv_values()
        key = config.get(env_var)
        if key:
            return key

        # 3. Interactive prompt (hides input)
        key = getpass.getpass(
            f"API key for {self.model.value} (env var {env_var}): "
        )
        if not key:
            raise RuntimeError(
                f"No API key provided for {self.model.value}. "
                f"Set {env_var} in the environment or .env file."
            )
        return key

    def _call_scoring_api(self, user_prompt: str) -> dict:
        """Call a managed-endpoint scoring API and return parsed JSON.

        Sends a chat-style payload to the ``/score`` endpoint and parses
        the JSON object from the response.
        """
        url = SCORING_ENDPOINTS[self.model]
        api_key = self._get_scoring_api_key()

        payload = json.dumps(
            {
                "input_data": {
                    "input_string": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                }
            }
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        max_retries = 5
        max_parse_retries = 2

        for parse_attempt in range(max_parse_retries + 1):
            for attempt in range(max_retries + 1):
                req = urllib.request.Request(
                    url, data=payload, headers=headers, method="POST"
                )
                try:
                    with urllib.request.urlopen(req) as resp:  # noqa: S310
                        body = resp.read().decode("utf-8")
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code == 429:
                        if attempt == max_retries:
                            raise RuntimeError(
                                f"Scoring API rate-limited after {max_retries + 1} attempts"
                            ) from exc
                        wait = min(15 * 2 ** attempt, 60)
                        print(
                            f"  Scoring API rate limited, retrying in {wait}s "
                            f"(attempt {attempt + 1}/{max_retries}) \u2026",
                            file=sys.stderr,
                        )
                        time.sleep(wait)
                    else:
                        raise RuntimeError(
                            f"Scoring API returned HTTP {exc.code}: {exc.read().decode()}"
                        ) from exc

            # The scoring endpoint may return the raw text or a JSON wrapper.
            # Try to parse the outer response first.
            try:
                outer = json.loads(body)
                # If the response is a list, take the first element.
                if isinstance(outer, list) and len(outer) > 0:
                    raw_text = outer[0] if isinstance(outer[0], str) else json.dumps(outer[0])
                elif isinstance(outer, dict):
                    # Could be the final JSON directly
                    raw_text = body
                else:
                    raw_text = str(outer)
            except json.JSONDecodeError:
                raw_text = body

            try:
                data = _extract_json(raw_text)
                Extraction(**data)
                return data
            except (json.JSONDecodeError, ValueError, ValidationError) as exc:
                if parse_attempt == max_parse_retries:
                    raise RuntimeError(
                        f"Failed to parse valid JSON after "
                        f"{max_parse_retries + 1} attempts. "
                        f"Last response:\n{raw_text}"
                    ) from exc
                print(
                    f"  JSON parse/validation failed ({exc!r}), "
                    f"retrying (attempt {parse_attempt + 1}/{max_parse_retries}) \u2026",
                    file=sys.stderr,
                )

        raise RuntimeError("Unexpected exit from retry loop")


# ---------------------------------------------------------------------------
# CLI
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


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Full pipeline: PDF → Document Intelligence → LLM extraction (unstructured)."
    )
    parser.add_argument("input", type=Path, help="A PDF file or directory of PDFs.")
    parser.add_argument("--year", type=str, default=None, help="Target fiscal year.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory.")
    parser.add_argument(
        "--model",
        type=str,
        default=Model.PHI_4.value,
        choices=[m.value for m in Model],
        help="Model deployment to use.",
    )
    parser.add_argument("--force", action="store_true", help="Re-run DI even if cached.")
    args = parser.parse_args()

    model = Model(args.model)
    inferencer = LLMUnstructuredInferencer(
        output_dir=args.output_dir,
        model=model,  # type: ignore[arg-type]  # CLI accepts any Model
        force=args.force,
    )

    pdfs = _collect_pdfs(args.input)
    if not pdfs:
        sys.exit(1)

    for pdf_path in pdfs:
        year = args.year or "2023"
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"Processing: {pdf_path.name}  (year={year}, model={model.value})", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)

        extraction = inferencer.extract(pdf_path, target_year=year)
        print(extraction.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
