#!/usr/bin/env python3
"""Normalize legacy Unlimited-OCR folders into RXN2 OCR result bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path


PAGE_MARKER = re.compile(r"(?m)^<PAGE>\s*$")
IMAGE_MARKER = re.compile(r"(?m)^!\[[^]]*\]\([^\n]+\)\s*$\n?")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_pages(markdown: str) -> list[str]:
    pages = []
    for value in PAGE_MARKER.split(markdown):
        text = IMAGE_MARKER.sub("", value).strip()
        if text:
            pages.append(text)
    if not pages:
        raise ValueError("legacy result contains no <PAGE> sections")
    return pages


def normalize_legacy_result(
    directory: Path,
    publication: str,
    source_pdf: Path,
    *,
    backup: bool = True,
) -> dict:
    directory = directory.resolve()
    source_pdf = source_pdf.resolve()
    result_md = directory / "result.md"
    success_path = directory / "_SUCCESS"
    if (directory / "result.json").is_file() and (directory / "pages.jsonl").is_file():
        return {"publication_number": publication, "status": "skipped", "reason": "already_normalized"}
    if not result_md.is_file() or not success_path.is_file() or not source_pdf.is_file():
        raise FileNotFoundError("legacy result.md, _SUCCESS, and source PDF are required")

    success = json.loads(success_path.read_text(encoding="utf-8"))
    pages = split_pages(result_md.read_text(encoding="utf-8"))
    publication = publication.replace(" ", "").upper()
    source_digest = sha256(source_pdf)
    completed_at = str(success.get("completed_at") or datetime.now(UTC).isoformat())
    job_id = f"{publication}-{source_digest[:12]}-legacy"
    page_records = [
        {
            "page": index,
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        for index, text in enumerate(pages, 1)
    ]
    combined_text = "\n\n".join(record["text"] for record in page_records)
    result = {
        "status": "succeeded",
        "job_id": job_id,
        "provider": "unlimited-ocr-colab",
        "model": str(success.get("model") or "baidu/Unlimited-OCR"),
        "model_revision": success.get("model_revision"),
        "publication_number": publication,
        "source_pdf": source_pdf.name,
        "source_sha256": source_digest,
        "page_count": len(page_records),
        "review_status": "unreviewed",
        "human_review_required": True,
        "created_at": completed_at,
        "completed_at": completed_at,
        "text": combined_text,
    }

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = directory.parent / f".{directory.name}.legacy-{timestamp}"
    staging = directory.parent / f".{directory.name}.normalized-{timestamp}"
    staging.mkdir(parents=False, exist_ok=False)
    try:
        (staging / "pages.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in page_records),
            encoding="utf-8",
        )
        (staging / "result.md").write_text(
            "\n\n".join(f"## Page {record['page']}\n\n{record['text']}" for record in page_records) + "\n",
            encoding="utf-8",
        )
        (staging / "result.txt").write_text(
            "\n\n".join(f"--- Page {record['page']} ---\n{record['text']}" for record in page_records) + "\n",
            encoding="utf-8",
        )
        (staging / "result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (staging / "_SUCCESS").write_text(
            json.dumps(
                {"job_id": job_id, "completed_at": completed_at, "source_sha256": source_digest},
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        if backup:
            directory.rename(backup_path)
        else:
            shutil.rmtree(directory)
        staging.rename(directory)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if backup_path.is_dir() and not directory.exists():
            backup_path.rename(directory)
        raise
    return {
        "publication_number": publication,
        "status": "succeeded",
        "job_id": job_id,
        "pages": len(page_records),
        "text_characters": len(combined_text),
        "backup": str(backup_path) if backup else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--publication", required=True)
    parser.add_argument("--source-pdf", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(normalize_legacy_result(args.directory, args.publication, args.source_pdf), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
