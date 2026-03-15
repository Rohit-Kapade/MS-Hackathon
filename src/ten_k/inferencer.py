"""Protocol for 10-K financial data extraction inferencers.

All inferencers accept a PDF path and optional target year, returning
a dict in the ground-truth nested format used by the evaluation harness.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ten_k.extraction import Extraction


@runtime_checkable
class Inferencer(Protocol):
    """Contract for 10-K financial data extraction strategies."""

    def extract(
        self,
        pdf_path: Path,
        *,
        target_year: str,
    ) -> Extraction:
        """Extract financial data from a single 10-K PDF.

        Args:
            pdf_path: Path to the input PDF file.
            target_year: Fiscal year to extract (e.g. "2023").

        Returns:
            An :class:`~ten_k.extraction.Extraction` with the extracted fields.
        """
        ...
