#!/usr/bin/env python3
"""Register a Drive-backed Unlimited-OCR result as unreviewed patent evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

try:
    from scripts.bulk_pipeline import DEFAULT_DB, DEFAULT_SCHEMA, connect, json_text, now, sha256_file
except ModuleNotFoundError:
    from bulk_pipeline import DEFAULT_DB, DEFAULT_SCHEMA, connect, json_text, now, sha256_file


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ingest_ocr_result(
    db: sqlite3.Connection,
    result_path: Path,
    publication_number: str,
    source_document: Path,
    source_url: str | None = None,
    model: str = "baidu/Unlimited-OCR",
) -> dict:
    result_path = result_path.resolve()
    source_document = source_document.resolve()
    if not result_path.is_file():
        raise FileNotFoundError(result_path)
    if source_document.suffix.casefold() != ".pdf" or not source_document.is_file():
        raise ValueError("source document must be an existing PDF")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") != "succeeded":
        raise ValueError("OCR result status must be succeeded")
    job_id = str(result.get("job_id") or "").strip()
    text = str(result.get("text") or "").strip()
    if not job_id or not text:
        raise ValueError("OCR result requires job_id and extracted text")
    publication = publication_number.replace(" ", "").upper()
    if not db.execute(
        "SELECT 1 FROM patent_document WHERE publication_number = ?", (publication,)
    ).fetchone():
        raise ValueError(f"unknown patent publication: {publication}")

    output_directory = result_path.parent
    artifacts = {}
    for name in ("result.json", "result.md", "result.txt"):
        path = output_directory / name
        if path.is_file():
            artifacts[name] = {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    metadata = {key: value for key, value in result.items() if key != "text"}
    metadata.update(
        {
            "publication_number": publication,
            "source_document": str(source_document),
            "source_document_sha256": sha256_file(source_document),
            "text_sha256": text_sha256(text),
            "text_length": len(text),
            "artifacts": artifacts,
            "location_status": "page_locations_unresolved",
            "acceptance_gate": "human_review_required",
        }
    )
    extraction_job_id = f"unlimited-ocr:{job_id}"
    db.execute(
        """INSERT INTO extraction_job
        (extraction_job_id, provider, model, prompt_sha256, input_sha256,
         response_sha256, source_url, raw_response_json, token_cost_json,
         status, review_status, created_at, completed_at)
        VALUES (?, 'unlimited-ocr-colab', ?, ?, ?, ?, ?, ?, '{}',
                'needs_review', 'unreviewed', ?, ?)
        ON CONFLICT(extraction_job_id) DO UPDATE SET
          response_sha256=excluded.response_sha256,
          source_url=excluded.source_url,
          raw_response_json=excluded.raw_response_json,
          status='needs_review',
          review_status='unreviewed',
          completed_at=excluded.completed_at""",
        (
            extraction_job_id,
            model,
            text_sha256("unlimited-ocr-colab-v1"),
            sha256_file(source_document),
            sha256_file(result_path),
            source_url,
            json_text(metadata),
            result.get("created_at") or now(),
            result.get("completed_at") or now(),
        ),
    )
    db.commit()
    return {
        "extraction_job_id": extraction_job_id,
        "publication_number": publication,
        "status": "needs_review",
        "review_status": "unreviewed",
        "evidence_spans_created": 0,
        "human_review_required": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--publication", required=True)
    parser.add_argument("--source-document", type=Path, required=True)
    parser.add_argument("--source-url")
    parser.add_argument("--model", default="baidu/Unlimited-OCR")
    args = parser.parse_args(argv)
    db = connect(args.db.resolve(), args.schema.resolve())
    try:
        result = ingest_ocr_result(
            db,
            args.result,
            args.publication,
            args.source_document,
            args.source_url,
            args.model,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
