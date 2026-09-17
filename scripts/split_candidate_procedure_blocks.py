#!/usr/bin/env python3
"""Split broad patent candidate spans into exact, review-gated procedure blocks.

This is deliberately conservative: it preserves the parent span, creates child
spans only at explicit procedure headings, and queues only children that show
both executed-operation and outcome language.  It never labels a child as a
performed reaction and never creates accepted chemistry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"
DEFAULT_REPORT = ROOT / "data" / "processed" / "audits" / "candidate-procedure-split.json"
EXTRACTOR_VERSION = "rxn2-candidate-procedure-split-v1"
MINIMUM_CHARS = 180
MAXIMUM_CHARS = 12_000

HEADING = re.compile(
    r"(?im)^(?:\[\d{1,6}\]\s*)?(?:(?:reference|comparative)\s+)?"
    r"(?:example|preparation|intermediate|step|stage)\s*(?:[-.:]\s*|\s+)"
    r"(?:\d+[A-Z]?|[A-Z])\b[^\n]*"
)
OPERATIONS = re.compile(
    r"\b(?:added|charged|stirred|heated|cooled|treated|combined|dissolved|"
    r"refluxed|hydrogenated|filtered)\b",
    re.I,
)
OUTCOME = re.compile(
    r"\b(?:obtained|gave|yielded|isolated|afforded|title compound|conversion)\b",
    re.I,
)
REFERENCE_ONLY = re.compile(
    r"\b(?:prepared as described|according to example|described in example|"
    r"literature example|as reported)\b",
    re.I,
)


def stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def procedure_blocks(text: str) -> list[tuple[int, int, str]]:
    """Return complete heading-delimited blocks, retaining exact source offsets."""
    headings = list(HEADING.finditer(text))
    if len(headings) < 2:
        return []
    blocks: list[tuple[int, int, str]] = []
    for index, heading in enumerate(headings):
        start = heading.start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[start:end].strip()
        leading = len(text[start:end]) - len(text[start:end].lstrip())
        absolute_start = start + leading
        absolute_end = absolute_start + len(block)
        if MINIMUM_CHARS <= len(block) <= MAXIMUM_CHARS:
            blocks.append((absolute_start, absolute_end, block))
    return blocks


def is_relation_ready(text: str) -> bool:
    return bool(OPERATIONS.search(text) and OUTCOME.search(text) and not REFERENCE_ONLY.search(text))


def split_candidates(db_path: Path, apply: bool) -> dict:
    now = datetime.now(UTC).isoformat()
    connection = sqlite3.connect(db_path, timeout=90)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=90000")
    rows = list(connection.execute(
        """SELECT * FROM evidence_span
           WHERE evidence_status='candidate'
           ORDER BY publication_number, evidence_span_id"""
    ))
    planned: list[dict] = []
    for row in rows:
        for index, (start, end, text) in enumerate(procedure_blocks(row["evidence_text"]), 1):
            evidence_id = stable_id("evidence-span", row["evidence_span_id"], start, end)
            planned.append({
                "parent_evidence_span_id": row["evidence_span_id"],
                "evidence_span_id": evidence_id,
                "index": index,
                "char_start": row["char_start"] + start,
                "char_end": row["char_start"] + end,
                "text": text,
                "ready": is_relation_ready(text),
                "row": row,
            })
    report = {
        "schema_version": EXTRACTOR_VERSION,
        "created_at": now,
        "apply": apply,
        "parents_scanned": len(rows),
        "child_blocks": len(planned),
        "relation_ready_blocks": sum(item["ready"] for item in planned),
        "automatic_acceptance": False,
        "review_status": "needs_review",
    }
    if not apply:
        connection.close()
        return report
    try:
        connection.execute("BEGIN IMMEDIATE")
        for item in planned:
            row = item["row"]
            paragraph = f"{row['paragraph_id'] or row['evidence_span_id']}#procedure-{item['index']}"
            connection.execute(
                """INSERT OR IGNORE INTO evidence_span
                   (evidence_span_id, publication_number, source_id, artifact_sha256,
                    section_type, paragraph_id, char_start, char_end, evidence_text,
                    text_sha256, evidence_status, extraction_method, extractor_version,
                    review_status, source_url, retrieved_at, license_code, redistribution_class)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, 'needs_review', ?, ?, ?, ?)""",
                (item["evidence_span_id"], row["publication_number"], row["source_id"],
                 row["artifact_sha256"], row["section_type"], paragraph,
                 item["char_start"], item["char_end"], item["text"], sha256_text(item["text"]),
                 row["extraction_method"], EXTRACTOR_VERSION, row["source_url"],
                 row["retrieved_at"], row["license_code"], row["redistribution_class"]),
            )
            if item["ready"]:
                identity = f"{item['evidence_span_id']}:segmented"
                connection.execute(
                    """INSERT OR IGNORE INTO pipeline_job
                       (pipeline_job_id, job_type, input_identity, input_sha256, status,
                        attempt_count, queued_at, result_json)
                       VALUES (?, 'relation_extraction', ?, ?, 'queued', 0, ?, ?)""",
                    (stable_id("pipeline-job", "relation_extraction", identity), identity,
                     sha256_text(item["text"]), now, json.dumps({
                         "evidence_span_id": item["evidence_span_id"],
                         "provider_mode": "auto",
                         "candidate_status": "segmented_candidate",
                         "parent_evidence_span_id": item["parent_evidence_span_id"],
                         "segment_index": item["index"],
                     }, sort_keys=True)),
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return report


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = split_candidates(args.db, args.apply)
    write_report(args.report, report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
