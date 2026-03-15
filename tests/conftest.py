"""Shared configurable test fixtures.

Define test schemas here so they are easy to change or extend.  All test
modules can use these fixtures by name.
"""

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configurable test data — edit or extend these to add new scenarios
# ---------------------------------------------------------------------------

DICT_GROUND_TRUTH_SCHEMA: dict = {
    "id": "B1",
    "query": "ESG Rating scorecard - ENVIRONMENT - Carbon Emissions - KEY ISSUE ASSESSMENT",
    "prompt": "Extract RISK EXPOSURE ASSESSMENT and RISK MANAGEMENT ASSESSMENT",
    "context": "KEY ISSUE ASSESSMENT",
    "ground_truth": {
        "RISK EXPOSURE ASSESSMENT": {"Company": "6.4", "Industry": "6.4"},
        "RISK MANAGEMENT ASSESSMENT": {"Company": "6.5", "Industry": "6.1"},
    },
}

LIST_GROUND_TRUTH_SCHEMA: dict = {
    "id": "B7",
    "query": "ESG Rating scorecard - Carbon Emissions - Description & Practice",
    "prompt": (
        "Extract the table with columns 'Description', 'Company Practice', "
        "'Best Practice', 'Practice Score'"
    ),
    "context": "ENVIRONMENT - Carbon Emissions - Description & Practice",
    "page": "3,4",
    "ground_truth": [
        {
            "Description": "Aggressiveness of the company's reduction target",
            "Company Practice": "Aggressive target with a low base",
            "Best Practice": "Aggressive target with a low base",
            "Practice Score": "TOP",
        },
        {
            "Description": "Demonstrated track record of achieving targets",
            "Company Practice": "Not on pace to achieve current reduction target",
            "Best Practice": "Previously set & met targets",
            "Practice Score": "MID",
        },
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def dict_schema() -> dict:
    """A ground-truth schema whose ``ground_truth`` value is a dict."""
    return json.loads(json.dumps(DICT_GROUND_TRUTH_SCHEMA))  # deep copy


@pytest.fixture()
def list_schema() -> dict:
    """A ground-truth schema whose ``ground_truth`` value is a list."""
    return json.loads(json.dumps(LIST_GROUND_TRUTH_SCHEMA))  # deep copy



