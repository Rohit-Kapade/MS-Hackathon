# 10-K information extraction with different models

## Goal

Compare multiple Azure Foundry models on structured financial extraction from
SEC 10-K filings to answer three questions:

1. **Model ranking:** Which model achieves the highest extraction accuracy
   across the five-document evaluation set, and how large are the gaps between
   models?
2. **Error patterns:** Where do extractions fail most often — which financial
   fields, statement types, or document characteristics drive the majority of
   misses — and what does that reveal about opportunities to improve accuracy?

## Approach

### Dataset

Five public-company 10-K filings (2023–2024 fiscal years). A heuristic
pre-filter narrows each full report down to the consolidated financial
statements (balance sheet, income statement, cash-flow statement), which contain
the target information. All model comparisons run on the same pre-filtered
source pages.

### Document preprocessing

Azure Document Intelligence extracts markdown tables from the filtered pages.
The resulting markdown is identical across all model runs, so differences in
extraction accuracy reflect only the extraction method, not the OCR step.

### Baseline — deterministic table matching

A no-LLM baseline pairs Document Intelligence table rows with target financial
fields via deterministic string-alias matching. This establishes a floor that
any LLM-based approach should exceed.

### LLM extraction

Three models are compared:

| Model | Structured outputs | Prompt style |
| --- | --- | --- |
| GPT-5.1 | Yes | Pydantic response schema |
| GPT-5.1 Codex | Yes | Pydantic response schema |
| Phi-4 | No | JSON-return prompt + post-processing |

Models that support structured outputs receive a Pydantic schema describing the
39 target fields. Phi-4 is prompted to return JSON and the output is
post-processed to extract values.

None of the prompts are optimized — this experiment compares out-of-the-box
model capability, not prompt engineering.

### Evaluation

Hit rate over the 39 financial fields: a field is a hit when the extracted value
matches ground truth within 0.1% relative tolerance (with an additional 1 000×
scaling check for unit mismatches). See `src/ten_k/hit_rate.py`.

### Code version

We run the evaluations on commit `ea3cbfb083f755f64bc1630eead7e122267ad3cb`.

## Results

### Overall hit rate by method

| Method | BellRing | DoorDash | Floor & Decor | Maravai | Newell | **Average** |
| --- | --- | --- | --- | --- | --- | --- |
| Table match (baseline) | 87% | 44% | 51% | 59% | 59% | **60%** |
| GPT-5.1 | 90% | 59% | 79% | 77% | 85% | **78%** |
| GPT-5.1 Codex | 90% | 62% | 85% | 74% | 85% | **79%** |
| Phi-4 | 72% | 44% | 77% | 64% | 72% | **66%** |

## Discussion

LLM-based extraction outperforms the deterministic table-match baseline across
all five filings. GPT-5.1 and GPT-5.1 Codex achieve near-identical average hit
rates. Phi-4 lands only slightly above the baseline, indicating lower extraction
quality relative to the GPT-5.1 family.

### Limitations

Several factors influence the absolute numbers reported here. Because every
method shares the same pre-filtered input and ground truth, relative comparisons
between methods remain valid even where absolute accuracy is affected.

1. Ground-truth errors exist in the evaluation dataset. Manual spot-checks
   revealed incorrect reference values for a handful of fields, which penalize
   all methods equally.
2. The heuristic page filter sometimes excludes pages that contain target
   information. This caps the achievable hit rate for every method but does not
   distort the ranking.
3. Edge cases around zero values, absent values, and NaNs are not handled
   consistently. The ground truth occasionally misrepresents these cases, and
   our extraction pipeline does not yet distinguish between "value is zero" and
   "value is not reported."
4. Some target fields are derived from (or calculable out of) other fields. When
   a model extracts the constituent values correctly but produces a different
   derived total, the evaluation counts this as a miss. Understanding when
   derived values appear in filings, and whether to extract or compute them,
   requires further discussion with the business.
5. We did not add any variability analysis across multiple runs to understand
   how deterministic the models are on extracting the data.
6. Ground truth data sometimes has an inconsistent/incorrect unit of
   measurement. The reports were in thousands/millions of dollars, but the
   ground truth did not always match the unit of the report.
7. We have no estimate of the uncertainty/error bars on the measurements. Since
   we only have five entries in the eval set, all results are at best
   indicators, but no definitive results.
