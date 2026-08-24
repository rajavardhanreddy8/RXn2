#!/usr/bin/env python3
"""Split oversized failed relation jobs into provenance-preserving evidence chunks."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-provisional.sqlite"


def stable_id(prefix: str, *parts: object) -> str:
    value = "\x1f".join(str(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def chunks(text: str, maximum: int) -> list[tuple[int, int, str]]:
    if maximum < 2000:
        raise ValueError("maximum must be at least 2000 characters")
    result = []
    start = 0
    while start < len(text):
        proposed = min(start + maximum, len(text))
        end = proposed
        if proposed < len(text):
            floor = start + maximum // 2
            for marker in ("\n\n", "\n", ". "):
                boundary = text.rfind(marker, floor, proposed)
                if boundary >= floor:
                    end = boundary + len(marker)
                    break
        piece = text[start:end]
        if piece.strip():
            result.append((start, end, piece))
        start = end
    return result


def split_failed(db_path: Path, maximum: int, apply: bool) -> dict:
    db = sqlite3.connect(db_path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    rows = list(db.execute(
        """SELECT p.*, e.* FROM pipeline_job p
           JOIN evidence_span e
             ON e.evidence_span_id=json_extract(p.result_json, '$.evidence_span_id')
           WHERE p.job_type='relation_extraction' AND p.status IN ('queued','failed')
             AND (length(e.evidence_text)>? OR p.error_text LIKE '%413 Payload Too Large%')
           ORDER BY length(e.evidence_text) DESC""",
        (maximum,),
    ))
    report = {"oversized_jobs": len(rows), "chunks": 0, "applied": apply, "jobs": []}
    if not apply:
        for row in rows:
            parts = chunks(row["evidence_text"], maximum)
            report["chunks"] += len(parts)
            report["jobs"].append({"pipeline_job_id": row["pipeline_job_id"], "characters": len(row["evidence_text"]), "chunks": len(parts)})
        db.close()
        return report

    now = datetime.now(UTC).isoformat()
    db.execute("BEGIN IMMEDIATE")
    try:
        for row in rows:
            payload = json.loads(row["result_json"] or "{}")
            child_ids = []
            parts = chunks(row["evidence_text"], maximum)
            for index, (start, end, text) in enumerate(parts, 1):
                evidence_id = stable_id("evidence-span", row["evidence_span_id"], start, end)
                digest = hashlib.sha256(text.encode()).hexdigest()
                paragraph = f"{row['paragraph_id'] or row['evidence_span_id']}#chunk-{index}"
                db.execute(
                    """INSERT OR IGNORE INTO evidence_span
                       (evidence_span_id, publication_number, source_id, artifact_sha256,
                        section_type, paragraph_id, char_start, char_end, evidence_text,
                        text_sha256, evidence_status, extraction_method, extractor_version,
                        review_status, source_url, retrieved_at, license_code, redistribution_class)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'needs_review', ?, ?, ?, ?)""",
                    (evidence_id, row["publication_number"], row["source_id"], row["artifact_sha256"],
                     row["section_type"], paragraph, (row["char_start"] or 0) + start,
                     (row["char_start"] or 0) + end, text, digest, row["evidence_status"],
                     row["extraction_method"], "rxn2-span-split-v1", row["source_url"],
                     row["retrieved_at"], row["license_code"], row["redistribution_class"]),
                )
                input_identity = f"{evidence_id}:auto"
                job_id = stable_id("pipeline-job", "relation_extraction", input_identity)
                child_payload = {
                    "evidence_span_id": evidence_id,
                    "provider_mode": payload.get("provider_mode", "auto"),
                    "candidate_status": payload.get("candidate_status", "evidence_only"),
                    "parent_evidence_span_id": row["evidence_span_id"],
                    "chunk_index": index,
                    "chunk_count": len(parts),
                }
                db.execute(
                    """INSERT OR IGNORE INTO pipeline_job
                       (pipeline_job_id, job_type, input_identity, input_sha256, status,
                        attempt_count, queued_at, result_json)
                       VALUES (?, 'relation_extraction', ?, ?, 'queued', 0, ?, ?)""",
                    (job_id, input_identity, digest, now, json.dumps(child_payload, sort_keys=True)),
                )
                child_ids.append(job_id)
            payload["split_child_job_ids"] = child_ids
            db.execute(
                """UPDATE pipeline_job SET status='skipped', completed_at=?, result_json=?,
                   error_text=? WHERE pipeline_job_id=? AND status IN ('queued','failed')""",
                (now, json.dumps(payload, sort_keys=True),
                 f"oversized evidence replaced by {len(child_ids)} provenance-preserving chunks",
                 row["pipeline_job_id"]),
            )
            report["chunks"] += len(parts)
            report["jobs"].append({"pipeline_job_id": row["pipeline_job_id"], "characters": len(row["evidence_text"]), "chunks": len(parts)})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--maximum", type=int, default=12000)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(split_failed(args.db, args.maximum, args.apply), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())