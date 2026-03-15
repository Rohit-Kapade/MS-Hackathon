from enum import Enum
from typing import Literal


class Model(str, Enum):
    GPT_5_1 = "gpt-5.1"
    GPT_5_1_CODEX = "gpt-5.1-codex"
    PHI_4 = "Phi-4"
    GPT_4_1 = "gpt-4.1-191849"
    GPT_4_1_MINI = "gpt-4.1-mini-939006"
    FINANCIAL_REPORTS_V2 = "financial-reports-analysis-v2"


StructuredModel = Literal[Model.GPT_5_1, Model.GPT_5_1_CODEX, Model.GPT_4_1, Model.GPT_4_1_MINI]
UnstructuredModel = Literal[Model.PHI_4, Model.FINANCIAL_REPORTS_V2]

# Models that use a custom scoring endpoint instead of Azure Foundry.
SCORING_MODELS: frozenset[Model] = frozenset({Model.FINANCIAL_REPORTS_V2})

SCORING_ENDPOINTS: dict[Model, str] = {
    Model.FINANCIAL_REPORTS_V2: (
        "https://foundryhack-sn-hub-proj-tycgo.switzerlandnorth.inference.ml.azure.com/score"
    ),
}

SCORING_API_KEY_ENV_VARS: dict[Model, str] = {
    Model.FINANCIAL_REPORTS_V2: "FINANCIAL_REPORTS_V2_API_KEY",
}

_STRUCTURED_MODELS: frozenset[Model] = frozenset(
    {Model.GPT_5_1, Model.GPT_5_1_CODEX, Model.GPT_4_1, Model.GPT_4_1_MINI}
)


def supports_structured_output(model: Model) -> bool:
    """Return True if the model supports the structured-output API."""
    return model in _STRUCTURED_MODELS
