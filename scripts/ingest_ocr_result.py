#!/usr/bin/env python3
"""Register a Drive-backed OCR result as unreviewed patent evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

try:
    from scripts.bulk_pipeline import DEFAULT_DB, DEFAULT_SCHEMA, connect, json_text, now, sha256_file
except ModuleNotFoundError:
    from bulk_pipeline import DEFAULT_DB, DEFAULT_SCHEMA, connect, json_text, now, sha256_file


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROCESSED_ROOT = ROOT / "data" / "processed" / "ocr"
MAX_PROCESSED_BYTES = 256 * 1024 * 1024
DEFAULT_PROVIDER = "unlimited-ocr-colab"
DEFAULT_MODEL = "baidu/Unlimited-OCR"


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ingest_ocr_result(
    db: sqlite3.Connection,
    result_path: Path,
    publication_number: str,
    source_document: Path,
    source_url: str | None = None,
    model: str | None = None,
    source_document_sha256: str | None = None,
    processed_root: Path | None = None,
    provider: str | None = None,
) -> dict:
    result_path = result_path.resolve()
    source_document = source_document.resolve()
    if not result_path.is_file():
        raise FileNotFoundError(result_path)
    if source_document.suffix.casefold() != ".pdf":
        raise ValueError("source document must identify a PDF")
    if source_document_sha256:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", source_document_sha256):
            raise ValueError("source document SHA-256 must contain 64 hex characters")
        input_sha256 = source_document_sha256.lower()
    elif source_document.is_file():
        input_sha256 = sha256_file(source_document)
    else:
        raise ValueError(
            "source document is unavailable locally; provide its cloud-computed SHA-256"
        )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") != "succeeded":
        raise ValueError("OCR result status must be succeeded")
    job_id = str(result.get("job_id") or "").strip()
    text = str(result.get("text") or "").strip()
    if not job_id or not text:
        raise ValueError("OCR result requires job_id and extracted text")
    provider_name = str(provider or result.get("provider") or DEFAULT_PROVIDER).strip()
    model_name = str(model or result.get("model") or DEFAULT_MODEL).strip()
    if not provider_name or not model_name:
        raise ValueError("OCR result requires provider and model")
    publication = publication_number.replace(" ", "").upper()
    if not db.execute(
        "SELECT 1 FROM patent_document WHERE publication_number = ?", (publication,)
    ).fetchone():
        raise ValueError(f"unknown patent publication: {publication}")

    output_directory = result_path.parent
    source_artifacts = [
        output_directory / name
        for name in ("result.json", "result.md", "result.txt", "pages.jsonl")
    ]
    total_bytes = sum(path.stat().st_size for path in source_artifacts if path.is_file())
    if total_bytes > MAX_PROCESSED_BYTES:
        raise ValueError(
            f"OCR processed output exceeds the {MAX_PROCESSED_BYTES}-byte local limit"
        )
    local_directory = None
    if processed_root:
        local_directory = processed_root.resolve() / job_id
        local_directory.mkdir(parents=True, exist_ok=True)
        for source in source_artifacts:
            if not source.is_file():
                continue
            target = local_directory / source.name
            partial = target.with_suffix(target.suffix + ".partial")
            try:
                shutil.copy2(source, partial)
                if sha256_file(partial) != sha256_file(source):
                    raise RuntimeError(f"processed OCR checksum mismatch: {source.name}")
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
    artifacts = {}
    for name in ("result.json", "result.md", "result.txt", "pages.jsonl"):
        path = (local_directory or output_directory) / name
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
            "source_document_sha256": input_sha256,
            "text_sha256": text_sha256(text),
            "text_length": len(text),
            "artifacts": artifacts,
            "location_status": "page_locations_unresolved",
            "acceptance_gate": "human_review_required",
        }
    )
    extraction_job_id = f"{provider_name}:{job_id}"
    db.execute(
        """INSERT INTO extraction_job
        (extraction_job_id, provider, model, prompt_sha256, input_sha256,
         response_sha256, source_url, raw_response_json, token_cost_json,
         status, review_status, created_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}',
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
            provider_name,
            model_name,
            text_sha256(f"{provider_name}-v1"),
            input_sha256,
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
        "processed_output": str(local_directory) if local_directory else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--publication", required=True)
    parser.add_argument("--source-document", type=Path, required=True)
    parser.add_argument("--source-document-sha256")
    parser.add_argument("--processed-root", type=Path, default=DEFAULT_PROCESSED_ROOT)
    parser.add_argument("--source-url")
    parser.add_argument("--provider")
    parser.add_argument("--model")
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
            args.source_document_sha256,
            args.processed_root,
            args.provider,
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
