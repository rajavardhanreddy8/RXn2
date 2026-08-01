import pytest

from scripts.classify_patent_pdf import classify_text_pages


def test_text_rich_pdf_prefers_text_extraction():
    result = classify_text_pages(["A" * 600, "B" * 600])
    assert result["extraction_mode"] == "text_extraction"
    assert result["review_status"] == "unreviewed"


def test_scanned_or_blank_pdf_requires_ocr():
    result = classify_text_pages(["", "few words"])
    assert result["extraction_mode"] == "ocr"
    with pytest.raises(ValueError, match="no pages"):
        classify_text_pages([])
