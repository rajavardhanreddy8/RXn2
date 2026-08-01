#!/usr/bin/env python3
"""Classify a patent PDF for text extraction or OCR without accepting evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify_text_pages(pages: list[str]) -> dict:
    page_count = len(pages)
    if not page_count:
        raise ValueError("PDF has no pages")
    characters = sum(len(page.strip()) for page in pages)
    text_pages = sum(bool(page.strip()) for page in pages)
    threshold = max(1_000, page_count * 100)
    mode = "text_extraction" if characters >= threshold and text_pages / page_count >= 0.8 else "ocr"
    return {
        "page_count": page_count,
        "text_characters": characters,
        "pages_with_text": text_pages,
        "text_threshold": threshold,
        "extraction_mode": mode,
        "next_action": (
            "extract embedded text and retain page locations"
            if mode == "text_extraction"
            else "submit the PDF to the Drive-backed OCR worker"
        ),
        "review_status": "unreviewed",
        "acceptance_gate": "human_review_required",
    }


def classify_pdf(path: Path, publication_number: str | None = None) -> dict:
    try:
        import fitz
    except ModuleNotFoundError as error:
        raise RuntimeError("PyMuPDF is required; install requirements-pipeline.txt") from error
    path = path.resolve()
    if not path.is_file() or path.suffix.casefold() != ".pdf":
        raise ValueError("provide an existing PDF")
    with fitz.open(path) as document:
        result = classify_text_pages([page.get_text("text") for page in document])
    return {
        "publication_number": publication_number,
        "source_document": str(path),
        "source_document_sha256": sha256_file(path),
        **result,
    }


def write_report(result: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    partial.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publication")
    args = parser.parse_args()
    result = classify_pdf(args.pdf, args.publication)
    write_report(result, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
