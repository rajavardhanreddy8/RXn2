#!/usr/bin/env python3
"""Export queued RXN2 relation jobs to a compact JSONL file for Colab."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export(db_path: Path, output: Path, limit: int = 0) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    query = """
        SELECT p.pipeline_job_id, p.result_json, e.evidence_span_id,
               e.publication_number, e.evidence_text, e.source_url
        FROM pipeline_job p
        JOIN evidence_span e
          ON e.evidence_span_id=json_extract(p.result_json, '$.evidence_span_id')
        WHERE p.job_type='relation_extraction'
          AND p.status='queued'
          AND length(e.evidence_text) <= 15000
        ORDER BY CASE json_extract(p.result_json, '$.candidate_status')
                   WHEN 'participant_roles_partial' THEN 0 ELSE 1 END,
                 p.attempt_count, length(e.evidence_text), p.queued_at
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    count = 0
    with sqlite3.connect(db_path) as db, output.open("w", encoding="utf-8") as out:
        db.row_factory = sqlite3.Row
        for row in db.execute(query):
            payload = {
                "pipeline_job_id": row["pipeline_job_id"],
                "evidence_span_id": row["evidence_span_id"],
                "publication_number": row["publication_number"],
                "evidence_text": row["evidence_text"],
                "source_url": row["source_url"],
                "candidate_status": json.loads(row["result_json"]).get("candidate_status"),
            }
            out.write(json.dumps(payload, ensure_ascii=False) + "\n")
            count += 1
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "database": str(db_path),
        "records": count,
        "output": str(output),
        "sha256": sha256_file(output),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    report = export(args.db, args.output, args.limit)
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
