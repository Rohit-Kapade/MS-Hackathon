"""Pydantic model for extracted 10-K financial data.

Used as the shared return type across all inferencer implementations
and the evaluation harness.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Extraction(BaseModel):
    """Flat representation of all 39 financial fields extracted from a 10-K.

    Accepts both flat dicts and the nested ground-truth JSON format
    (income_statement.statement.*, cash_flow.*, balance_sheet.*).
    """

    model_config = ConfigDict(populate_by_name=True)

    # -- Income statement -----------------------------------------------------
    revenue: float | None = Field(None, validation_alias="Revenue")
    cogs: float | None = Field(None, validation_alias="COGS")
    gross_profit: float | None = Field(None, validation_alias="Gross Profit")
    sga: float | None = Field(None, validation_alias="SG&A")
    total_operating_expenses: float | None = Field(
        None, validation_alias="Total Operating Expenses"
    )
    taxes: float | None = Field(None, validation_alias="Plus: Taxes")
    interest_expense: float | None = Field(
        None, validation_alias="Plus: Interest Expense"
    )
    interest_income: float | None = Field(
        None, validation_alias="Less: Interest Income (if available)"
    )
    da: float | None = Field(None, validation_alias="Plus: D&A")

    # -- Operating activities -------------------------------------------------
    net_income: float | None = Field(None, validation_alias="Net Income")
    cash_from_operations: float | None = Field(
        None, validation_alias="Cash from Operations"
    )
    change_in_cash: float | None = Field(None, validation_alias="Change in Cash")
    changes_in_nwc: float | None = Field(None, validation_alias="Less: Changes in NWC")

    # -- Investing activities -------------------------------------------------
    cash_from_investing: float | None = Field(
        None, validation_alias="Cash from Investing"
    )
    capex: float | None = Field(None, validation_alias="Less: Capex")
    acquisitions: float | None = Field(None, validation_alias="Acquisitions")
    divestitures: float | None = Field(None, validation_alias="Divestitures")

    # -- Financing activities -------------------------------------------------
    cash_from_financing: float | None = Field(
        None, validation_alias="Cash from Financing"
    )
    exchange_rates_other: float | None = Field(
        None, validation_alias="Effect of Exchange Rates / Other"
    )
    cash_interest_net: float | None = Field(
        None, validation_alias="Less: Cash interest (net)"
    )
    cash_taxes: float | None = Field(None, validation_alias="Less: Cash taxes")
    dividends: float | None = Field(None, validation_alias="Dividends")
    net_share_issuance: float | None = Field(
        None, validation_alias="Net Share Issuance (Repurchase)"
    )
    net_debt_issuance: float | None = Field(
        None, validation_alias="Net Debt Issuance (Repayment)"
    )

    # -- Assets ---------------------------------------------------------------
    cash: float | None = Field(None, validation_alias="Cash")
    accounts_receivable: float | None = Field(
        None, validation_alias="Accounts Receivable"
    )
    inventory: float | None = Field(None, validation_alias="Inventory")
    current_assets: float | None = Field(None, validation_alias="Current Assets")
    goodwill: float | None = Field(None, validation_alias="Goodwill")
    other_intangibles: float | None = Field(None, validation_alias="Other Intangibles")
    total_assets: float | None = Field(None, validation_alias="Total Assets")

    # -- Liabilities ----------------------------------------------------------
    short_term_debt: float | None = Field(None, validation_alias="Short Term Debt")
    accounts_payable: float | None = Field(None, validation_alias="Accounts Payable")
    accrued_expenses: float | None = Field(None, validation_alias="Accrued Expenses")
    deferred_revenue: float | None = Field(None, validation_alias="Deferred Revenue")
    current_liabilities: float | None = Field(
        None, validation_alias="Current Liabilities"
    )
    total_liabilities: float | None = Field(None, validation_alias="Total Liabilities")
    shareholders_equity: float | None = Field(
        None, validation_alias="Shareholders Equity"
    )
    operating_lease_obligations: float | None = Field(
        None, validation_alias="Operating Lease Obligations"
    )

    @model_validator(mode="before")
    @classmethod
    def _flatten_nested(cls, data: object) -> object:
        """Flatten the nested ground-truth JSON into leaf key→value pairs."""
        if not isinstance(data, dict):
            return data
        if not any(isinstance(v, dict) for v in data.values()):
            return data  # already flat
        flat: dict[str, object] = {}

        def _collect(obj: object) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, dict):
                        _collect(v)
                    else:
                        flat[k] = v

        _collect(data)
        return flat
