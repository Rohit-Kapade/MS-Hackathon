"""Tests for the ESG extraction and evaluation modules.

Unit tests for extraction utilities (schema hints, PDF loading, mode
validation) and evaluation logic (comparison helpers).
"""

import json
from pathlib import Path

import pytest

from src.run_extraction import (
    EXTRACTION_MODES,
    extract,
    load_pdf_images,
    load_pdf_text,
)


# ---------------------------------------------------------------------------
# make_schema_hint
# ---------------------------------------------------------------------------


class TestMakeSchemaHint:
    def test_dict_hint(self) -> None:
        gt = {"A": {"X": "1", "Y": "2"}, "B": "3"}
        assert make_schema_hint(gt) == {"A": {"X": "...", "Y": "..."}, "B": "..."}

    def test_list_hint(self) -> None:
        gt = [{"X": "1", "Y": "2"}]
        assert make_schema_hint(gt) == [{"X": "...", "Y": "..."}]

    def test_empty_list(self) -> None:
        assert make_schema_hint([]) == []

    def test_scalar(self) -> None:
        assert make_schema_hint("anything") == "..."


# ---------------------------------------------------------------------------
# load_pdf_text / load_pdf_images
# ---------------------------------------------------------------------------


class TestLoadPdfText:
    def test_raises_for_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_pdf_text(Path("/nonexistent/file.pdf"))

    def test_loads_real_pdf(self) -> None:
        """Smoke-test against the actual BASF PDF if available."""
        pdf = Path("ESG-files/data/BASF_Rating_Carbon_Emission.pdf")
        if not pdf.exists():
            pytest.skip("BASF PDF not available in this environment")
        text = load_pdf_text(pdf)
        assert isinstance(text, str)
        assert len(text) > 100
        assert "--- Page 1 ---" in text


class TestLoadPdfImages:
    def test_raises_for_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_pdf_images(Path("/nonexistent/file.pdf"))

    def test_loads_real_pdf(self) -> None:
        """Smoke-test against the actual BASF PDF if available."""
        pdf = Path("ESG-files/data/BASF_Rating_Carbon_Emission.pdf")
        if not pdf.exists():
            pytest.skip("BASF PDF not available in this environment")
        images = load_pdf_images(pdf)
        assert isinstance(images, list)
        assert len(images) >= 1
        import base64
        for img in images:
            assert isinstance(img, str)
            base64.b64decode(img)


# ---------------------------------------------------------------------------
# extract — mode validation (no Azure calls)
# ---------------------------------------------------------------------------


class TestExtractionMode:
    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="modes must be from"):
            extract(
                pdf_path="dummy.pdf",
                prompt="test",
                schema={"key": "value"},
                foundry_project_endpoint="https://example.azure.com",
                modes=("invalid",),
            )

    def test_valid_modes(self) -> None:
        assert set(EXTRACTION_MODES) == {"file", "vision", "text"}


# ---------------------------------------------------------------------------
# compare_with_ground_truth — dict schemas
# ---------------------------------------------------------------------------


class TestCompareDict:
    """Tests using the dict ground-truth fixture from conftest."""

    @pytest.fixture()
    def ground_truth(self, dict_schema: dict) -> dict:
        return dict_schema["ground_truth"]

    def test_all_match(self, ground_truth: dict) -> None:
        extracted = {
            "RISK EXPOSURE ASSESSMENT": {"Company": "6.4", "Industry": "6.4"},
            "RISK MANAGEMENT ASSESSMENT": {"Company": "6.5", "Industry": "6.1"},
        }
        report = compare_with_ground_truth(extracted, ground_truth)
        assert report["summary"]["accuracy_percent"] == 100.0
        assert report["summary"]["matches"] == 4
        assert report["summary"]["mismatches"] == 0

    def test_partial_match(self, ground_truth: dict) -> None:
        extracted = {
            "RISK EXPOSURE ASSESSMENT": {"Company": "6.4", "Industry": "9.9"},
            "RISK MANAGEMENT ASSESSMENT": {"Company": "6.5", "Industry": "6.1"},
        }
        report = compare_with_ground_truth(extracted, ground_truth)
        assert report["summary"]["matches"] == 3
        assert report["summary"]["mismatches"] == 1
        assert report["summary"]["accuracy_percent"] == 75.0

    def test_all_missing(self, ground_truth: dict) -> None:
        report = compare_with_ground_truth({}, ground_truth)
        assert report["summary"]["matches"] == 0
        assert report["summary"]["accuracy_percent"] == 0.0

    def test_field_level_detail(self, ground_truth: dict) -> None:
        extracted = {
            "RISK EXPOSURE ASSESSMENT": {"Company": "6.4", "Industry": "6.4"},
            "RISK MANAGEMENT ASSESSMENT": {"Company": "6.5", "Industry": "6.1"},
        }
        report = compare_with_ground_truth(extracted, ground_truth)
        field = report["field_results"]["RISK EXPOSURE ASSESSMENT"]
        assert field["Company"]["match"] is True
        assert field["Company"]["expected"] == "6.4"
        assert field["Company"]["extracted"] == "6.4"

    def test_string_normalisation(self) -> None:
        extracted = {"score": " 7.5 "}
        ground_truth = {"score": "7.5"}
        report = compare_with_ground_truth(extracted, ground_truth)
        assert report["summary"]["accuracy_percent"] == 100.0

    @pytest.mark.parametrize(
        "extracted,ground_truth,expected_accuracy",
        [
            ({"a": "1"}, {"a": "1"}, 100.0),
            ({"a": "1"}, {"a": "2"}, 0.0),
            ({}, {"a": "1"}, 0.0),
            ({"a": {"b": "1"}}, {"a": {"b": "1"}}, 100.0),
            ({"a": {"b": "1", "c": "2"}}, {"a": {"b": "1", "c": "X"}}, 50.0),
        ],
        ids=[
            "exact-match",
            "mismatch",
            "empty-extraction",
            "nested-match",
            "nested-partial",
        ],
    )
    def test_parametrized(
        self, extracted: dict, ground_truth: dict, expected_accuracy: float
    ) -> None:
        report = compare_with_ground_truth(extracted, ground_truth)
        assert report["summary"]["accuracy_percent"] == expected_accuracy


# ---------------------------------------------------------------------------
# compare_with_ground_truth — list (table-row) schemas
# ---------------------------------------------------------------------------


class TestCompareList:
    """Tests using the list ground-truth fixture from conftest."""

    @pytest.fixture()
    def ground_truth(self, list_schema: dict) -> list:
        return list_schema["ground_truth"]

    def test_all_match(self, ground_truth: list) -> None:
        extracted = json.loads(json.dumps(ground_truth))
        report = compare_with_ground_truth(extracted, ground_truth)
        assert report["summary"]["accuracy_percent"] == 100.0
        assert report["summary"]["matches"] == report["summary"]["total_fields"]

    def test_partial_match(self, ground_truth: list) -> None:
        extracted = [
            {**ground_truth[0], "Practice Score": "WRONG"},
            ground_truth[1],
        ]
        report = compare_with_ground_truth(extracted, ground_truth)
        total = report["summary"]["total_fields"]
        assert report["summary"]["matches"] == total - 1

    def test_empty_extraction(self, ground_truth: list) -> None:
        report = compare_with_ground_truth([], ground_truth)
        assert report["summary"]["matches"] == 0
        assert report["summary"]["accuracy_percent"] == 0.0

    def test_extra_rows_tracked(self, ground_truth: list) -> None:
        extracted = json.loads(json.dumps(ground_truth)) + [{"extra": "row"}]
        report = compare_with_ground_truth(extracted, ground_truth)
        assert report["summary"]["extra_extracted_rows"] == 1

    def test_non_list_extraction_returns_zero(self, ground_truth: list) -> None:
        report = compare_with_ground_truth({"wrong": "type"}, ground_truth)
        assert report["summary"]["accuracy_percent"] == 0.0

    @pytest.mark.parametrize(
        "extracted,ground_truth,expected_accuracy",
        [
            ([{"a": "1"}], [{"a": "1"}], 100.0),
            ([{"a": "2"}], [{"a": "1"}], 0.0),
            ([], [{"a": "1"}], 0.0),
            ([{"a": "1"}, {"a": "2"}], [{"a": "1"}, {"a": "2"}], 100.0),
        ],
        ids=["exact-match", "mismatch", "empty-extraction", "multi-row-match"],
    )
    def test_parametrized(
        self, extracted: list, ground_truth: list, expected_accuracy: float
    ) -> None:
        report = compare_with_ground_truth(extracted, ground_truth)
        assert report["summary"]["accuracy_percent"] == expected_accuracy
