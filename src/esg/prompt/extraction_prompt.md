You are an expert at extracting structured data from ESG controversy documents.

Given the following markdown content extracted from a PDF, extract ALL environment controversies and their case assessments.

The document contains these controversy indicators:

- Operational Waste (Non-Hazardous)
- Supply Chain Management
- Water Stress

For each controversy, extract:

1. The name of the controversy
2. The number of cases by severity (Very Severe, Severe, Moderate, Minor)
3. A list of all individual cases with their details

Return ONLY valid JSON (no markdown fencing) matching this exact structure:
{{
  "controversies": [
    {{
      "name": "<controversy indicator name>",
      "case_count": {{
        "Very Severe": 0,
        "Severe": 0,
        "Moderate": 0,
        "Minor": 0
      }},
      "cases": [
        {{
          "Case": "<one-liner summary of the case>",
          "Severity": "<Very Severe|Severe|Moderate|Minor>",
          "Score": <numeric score>,
          "Flag": "<Y|O|G|R>",
          "Role": "<role description>",
          "Status": "<status>",
          "Dates": {{
            "Last Reviewed Date": "<date>",
            "Case Initiated Date": "<date>",
            "Last Case Score Change Date": "<date>"
          }}
        }}
      ]
    }}
  ]
}}

If a controversy has no cases, include it with an empty cases array.
Extract dates in the format "Month DD, YYYY" (e.g. "February 02, 2026").

Document content:
{markdown}
