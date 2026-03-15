"""LLM-based inferencer: PDF → Document Intelligence → LLM extraction.

Extracts financial line items by sending PDFs through Azure Document
Intelligence for markdown extraction, then using an LLM with structured
outputs to intelligently identify and extract values.

Usage:
    uv run python -m ten_k.llm_extract.inferencer data/ --year 2023
    uv run python -m ten_k.llm_extract.inferencer data/report.pdf --year 2023 --model gpt-5.1
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
from dotenv import dotenv_values
from openai import RateLimitError
from pydantic import BaseModel, Field

from models import Model, StructuredModel
from ten_k.extract_tables import process_pdf_markdown
from ten_k.extraction import Extraction

SYSTEM_PROMPT = """\
You are a financial data extraction specialist. Extract values from the \
provided 10-K SEC filing markdown for the requested fiscal year.

Rules:
- Use financial knowledge to resolve label ambiguities (e.g. \
"Net revenues" → revenue, "Provision for income taxes" → taxes).
- Parenthesized values like (123.4) are negative: return -123.4.
- Strip $, commas, and whitespace but preserve the original magnitude — \
do NOT convert units. If the document says 288,945 return 288945.
- Return null when a line item is not present in the document."""


# ---------------------------------------------------------------------------
# Structured-output response model
# ---------------------------------------------------------------------------


class FinancialExtraction(BaseModel):
    """Structured output schema for LLM-based 10-K financial extraction.

    Field descriptions guide the model on which table rows to match.
    """

    # -- Income statement -----------------------------------------------------
    revenue: float | None = Field(
        description="Total revenue, net sales, or net revenue from the income statement."
    )
    cogs: float | None = Field(
        description="Cost of goods sold, cost of sales, or cost of revenue."
    )
    gross_profit: float | None = Field(
        description="Gross profit (revenue minus COGS)."
    )
    sga: float | None = Field(
        description="Selling, general and administrative expenses."
    )
    total_operating_expenses: float | None = Field(
        description="Total operating expenses."
    )
    taxes: float | None = Field(
        description="Income tax expense or provision for income taxes."
    )
    interest_expense: float | None = Field(
        description="Interest expense (net of interest income if reported together)."
    )
    interest_income: float | None = Field(
        description="Interest income, if separately disclosed."
    )
    da: float | None = Field(
        description="Depreciation and amortization expense."
    )

    # -- Cash flow: operating activities --------------------------------------
    net_income: float | None = Field(
        description="Net income or net earnings attributable to common stockholders."
    )
    cash_from_operations: float | None = Field(
        description="Net cash provided by (used in) operating activities."
    )
    change_in_cash: float | None = Field(
        description="Net increase (decrease) in cash and cash equivalents."
    )
    changes_in_nwc: float | None = Field(
        description="Changes in net working capital (aggregate of WC line items)."
    )

    # -- Cash flow: investing activities --------------------------------------
    cash_from_investing: float | None = Field(
        description="Net cash provided by (used in) investing activities."
    )
    capex: float | None = Field(
        description="Capital expenditures (purchases of property, plant & equipment)."
    )
    acquisitions: float | None = Field(
        description="Cash paid for business or asset acquisitions."
    )
    divestitures: float | None = Field(
        description="Proceeds from divestitures or sale of businesses."
    )

    # -- Cash flow: financing activities --------------------------------------
    cash_from_financing: float | None = Field(
        description="Net cash provided by (used in) financing activities."
    )
    exchange_rates_other: float | None = Field(
        description="Effect of exchange rate changes on cash, or other adjustments."
    )
    cash_interest_net: float | None = Field(
        description="Cash interest paid (net), from supplemental cash flow disclosure."
    )
    cash_taxes: float | None = Field(
        description="Income taxes paid in cash, from supplemental cash flow disclosure."
    )
    dividends: float | None = Field(
        description="Dividends paid to shareholders."
    )
    net_share_issuance: float | None = Field(
        description="Net share issuance or repurchase of common stock."
    )
    net_debt_issuance: float | None = Field(
        description="Net debt issuance or repayment of long-term debt."
    )

    # -- Balance sheet: assets ------------------------------------------------
    cash: float | None = Field(
        description="Cash and cash equivalents on the balance sheet."
    )
    accounts_receivable: float | None = Field(
        description="Accounts receivable or trade receivables (net)."
    )
    inventory: float | None = Field(
        description="Inventories."
    )
    current_assets: float | None = Field(
        description="Total current assets."
    )
    goodwill: float | None = Field(
        description="Goodwill."
    )
    other_intangibles: float | None = Field(
        description="Intangible assets (net), excluding goodwill."
    )
    total_assets: float | None = Field(
        description="Total assets."
    )

    # -- Balance sheet: liabilities -------------------------------------------
    short_term_debt: float | None = Field(
        description="Short-term debt or current portion of long-term debt."
    )
    accounts_payable: float | None = Field(
        description="Accounts payable."
    )
    accrued_expenses: float | None = Field(
        description="Accrued expenses, accrued liabilities, or other current liabilities."
    )
    deferred_revenue: float | None = Field(
        description="Deferred revenue or contract liabilities."
    )
    current_liabilities: float | None = Field(
        description="Total current liabilities."
    )
    total_liabilities: float | None = Field(
        description="Total liabilities."
    )
    shareholders_equity: float | None = Field(
        description="Total stockholders' equity (or deficit)."
    )
    operating_lease_obligations: float | None = Field(
        description="Operating lease obligations (right-of-use liabilities)."
    )


def _build_user_prompt(markdown: str, target_year: str) -> str:
    """Build the user-turn prompt containing DI markdown and target year."""
    return (
        f"## Target fiscal year\n{target_year}\n\n"
        f"## Document content\n{markdown}"
    )


class LLMInferencer:
    """Extracts financials by sending DI markdown to an LLM.

    Uses structured outputs to guarantee a valid response matching
    :class:`FinancialExtraction`. The :meth:`extract` method fulfils
    the :class:`~ten_k.inferencer.Inferencer` protocol.
    """

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        force: bool = False,
        model: StructuredModel = Model.GPT_5_1,
    ) -> None:
        self.output_dir = output_dir
        self.force = force
        self.model = model

    def extract(
        self,
        pdf_path: Path,
        *,
        target_year: str,
    ) -> Extraction:
        """Extract financial data from a single 10-K PDF.

        Runs DI markdown extraction, then sends the content to an LLM
        with structured outputs for intelligent value inference.
        """
        out = self.output_dir if self.output_dir is not None else pdf_path.parent

        md_path = process_pdf_markdown(
            pdf_path, output_dir=out, force=self.force
        )
        markdown = md_path.read_text(encoding="utf-8")
        user_prompt = _build_user_prompt(markdown, target_year)
        parsed = self._call_llm(user_prompt)

        return Extraction(**parsed.model_dump())

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _call_llm(self, user_prompt: str) -> FinancialExtraction:
        """Send the prompt to Azure Foundry using structured outputs.

        Retries up to 5 times on 429 rate-limit errors with exponential backoff.
        """
        config = dotenv_values()
        endpoint = config.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
        if not endpoint:
            raise RuntimeError(
                "AZURE_FOUNDRY_PROJECT_ENDPOINT not set in .env"
            )

        max_retries = 5
        with (
            AzureCliCredential() as credential,
            AIProjectClient(
                endpoint=endpoint, credential=credential
            ) as project_client,
        ):
            client = project_client.get_openai_client()

            for attempt in range(max_retries + 1):
                try:
                    response = client.responses.parse(
                        model=self.model.value,
                        instructions=SYSTEM_PROMPT,
                        input=[
                            {
                                "role": "user",
                                "content": user_prompt,
                            },
                        ],
                        text_format=FinancialExtraction,
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

        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("Model returned no structured output")

        return parsed


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
        description="Full pipeline: PDF → Document Intelligence → LLM extraction."
    )
    parser.add_argument("input", type=Path, help="A PDF file or directory of PDFs.")
    parser.add_argument("--year", type=str, default=None, help="Target fiscal year.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory.")
    parser.add_argument(
        "--model",
        type=str,
        default=Model.GPT_5_1.value,
        choices=[m.value for m in Model],
        help="Model deployment to use.",
    )
    parser.add_argument("--force", action="store_true", help="Re-run DI even if cached.")
    args = parser.parse_args()

    model = Model(args.model)
    inferencer = LLMInferencer(
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
