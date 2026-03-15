# Hackathon plan

We have two categories of documents, public SEC filings (10-K documents) and ESG
documents. For the hackathon, the goal is to extract structured information for
both types of documents.

We give highest priority to the SEC filings and extracting information from
there. As a follow-up (or even in parallel), we will work on extracting
information form the ESG documents. In the following, we list our plan for the
hackathon.

What this is not:

- a day-by-day plan. User stories/a day-by-day breakdown will be _generated_ out
  of this document.

## Tech Stack

- We will work in Dev Containers, either locally or on a VM.
- We use Azure Foundry and Foundry models. All models run in the cloud.
- We use Azure Foundry for evaluations.
- We may use Document Intelligence and other Azure services.
- We use Entra ID for auth, and avoid keys whenever possible.

## Setting the stage

The first part should be to get everyone aligned on what we're trying to do,
which technologies we're going to use and which problems we're trying to solve.
We should discuss the goals for the hackathon, both from a perspective of
learning and from a perspective of business value/deliverables. We can get
everyone set up with the development environment and ask how we want to divide &
conquer. We can gear towards more pairing, or more parallelization.

## Evaluation pipeline

We will build an evaluation pipeline that is built on top of the [Azure Foundry
evaluation features][foundry-evaluation]. The evaluation pipeline will _not_ use
the AI Evaluation SDK, but the newer Foundry evaluation features.

[foundry-evaluation]:
    https://learn.microsoft.com/azure/foundry/how-to/develop/cloud-evaluation?tabs=python

## Extracting structured information from SEC filings

At the moment, we have a corpus of five public, labeled 10-K documents, located
in <https://stfoundryhackmain.blob.core.windows.net/10k-10q>. We know which
format that we are going to extract from the documents; the extraction
requirements are defined as the following JSON structure:

```json
{
  "cash_flow": {
    "operating_activities": [
      "amortization",
      "depreciation",
      "cash_from_operations",
      "total_cash_from_operations",
      "less_changes_in_nwc",
      "net_income"
    ],
    "investing_activities": [
      "acquisitions",
      "less_capex",
      "cash_from_investing",
      "total_cash_from_investing",
      "divestitures"
    ],
    "financing_activities": [
      "less_cash_interest_net",
      "less_cash_taxes",
      "dividends",
      "cash_from_financing",
      "total_cash_from_financing",
      "effect_of_exchange_rates_other",
      "net_debt_issuance_repayment",
      "net_share_issuance_repurchase"
    ]
  },
  "income_and_operation": {
    "statement": [
      "gross_profit",
      "revenue",
      "total_revenue",
      "plus_interest_expense",
      "less_interest_income",
      "interest_income_or_expense",
      "sga",
      "plus_taxes",
      "cogs",
      "total_cogs",
      "total_operating_expenses"
    ]
  },
  "balance_sheet": {
    "asset": [
      "cash",
      "account_receivable",
      "inventory",
      "goodwill",
      "other_intangibles",
      "total_assets",
      "current_assets",
      "total_current_assets"
    ],
    "liability": [
      "short_term_debt",
      "account_payable",
      "accrued_expenses",
      "deferred_revenue",
      "shareholders_equity",
      "total_shareholders_equity",
      "operating_lease_obligations",
      "current_liabilities",
      "total_current_liabilities"
    ]
  }
}
```

Note that the documents are quite long. We have a good candidate heuristic for
preprocessing the files, as all information is concentrated on a few pages. By
extracting text from each page and searching for keywords like "Consolidated
Balance Sheet", "Consolidated Statements of Operations", "Consolidated
Statements of Cash Flows" we can narrow down the pages that contain the actual
information that we want to extract. This could allow us to get document sizes
from ~100 pages to ca 5 relevant pages.

To solve this problem, we need to define how we define whether the correct
information was extracted from the document. This could be based on an
approximate numerical match. We then need to define how we translate this into
an (aggregated) metric.

When we have set up the full evaluations, we need to find out our target system
setup. Aspects to consider:

- model performance: we must find out whether `gpt-5.1` or
  `financial-reports-analysis-v2` or plain `phi-4` performs equally well. We
  should likely use structured outputs if available.
- (stretch) document intelligence: we can consider out if layout extraction with
  document intelligence brings quality improvements over alternatives, such as
  PDF cracking with a PDF library or other approaches.
- (stretch) vision models: we can find out how vision (or multimodal) models
  perform if we pass the documents to them as images to them. This can also be
  in combination with document intelligence, by passing both to them (as seen
  with [ARGUS]).
- (stretch) [ColPali]: let's see if we feel this adventurous for cracking larger
  documents.
- (stretch) could Azure Content Understanding solve everything? How does it
  compare to Document Intelligence?

[ColPali]: https://github.com/illuin-tech/colpali
[ARGUS]: https://github.com/Azure-Samples/ARGUS

## Demo

We will demonstrate our approach and learnings on the last day. We should keep
in mind to prepare this demo and capture the most relevant information as we
hack.

## HVE

Hypervelocity Engineering is an explicit goal for this hack. Push yourself to
use GitHub Copilot as much as you can.

## (stretch) Labeling more 10-K documents

We only have five documents. We have more public 10-K documents from LSEG, which
we can label and use as additional data for our evaluations. The data is located
in <>.

## (stretch) ESG Rating Reports - Risk Management Assessment

We have a corpus of 12 ESG documents located in
<https://stfoundryhackmain.blob.core.windows.net/esg>. The main problem is that
for Rating Reports we cannot reliably extract the information from the "Risk
Management Assessment". Rohit is currently working on an evaluation set, which
will define the requirements of what information we expect to extract.

In the past, we have tried to use document cracking with PDF libraries and
Document Intelligence, but failed to extract the relevant information reliably.
We should integrate this into the evaluation pipeline and figure out which
approaches work best for reliably extracting the information. The things that we
can try are likely similar to the first use-case.

## (stretch) ESG Rating Reports - ESG Ratings Scorecard

The issue in the past with the ESG Ratings Scorecard was that it could contain
hyperlinks that are hard to follow/extract reliably. We should see how we solve
the information extraction needs for the ESG Ratings Scorecard best given these
complexities.
