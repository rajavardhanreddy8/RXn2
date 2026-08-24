#!/usr/bin/env python3
"""Validate and idempotently import RXN2 overnight Colab checkpoint parts."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import db as db_module
from apps.api.app.relations import RelationExtraction, persist_candidate, sha256_text, stable_id
from scripts.colab_relation_common import job_hash, validate_candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def records(run_root: Path) -> tuple[list[dict], list[dict]]:
    successes, failures = [], []
    for manifest_path in sorted((run_root / "manifest").glob("part-*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for kind, target in (("results", successes), ("retry", failures)):
            details = manifest.get(kind)
            if not details:
                continue
            artifact = run_root / details["path"]
            if sha256_file(artifact) != details["sha256"]:
                raise RuntimeError(f"checkpoint_checksum_mismatch:{artifact}")
            target.extend(json.loads(line) for line in artifact.read_text(encoding="utf-8").splitlines() if line.strip())
    return successes, failures


def validate_all(db_path: Path, run_root: Path) -> tuple[list[tuple[dict, RelationExtraction]], list[dict]]:
    successes, failures = records(run_root)
    jobs = {
        job["evidence_span_id"]: job
        for job in (json.loads(line) for line in (run_root / "jobs" / "jobs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    }
    prepared, seen = [], set()
    with sqlite3.connect(db_path) as db:
        for record in successes:
            digest = record["input_sha256"]
            if digest in seen:
                continue
            seen.add(digest)
            job = jobs.get(record["evidence_span_id"])
            if not job or job_hash(job) != digest:
                raise ValueError(f"input_hash_mismatch:{record['evidence_span_id']}")
            evidence = db.execute("SELECT evidence_text FROM evidence_span WHERE evidence_span_id=?", (record["evidence_span_id"],)).fetchone()
            if not evidence or evidence[0] != job["evidence_text"]:
                raise ValueError(f"database_evidence_mismatch:{record['evidence_span_id']}")
            validate_candidate(record["candidate"], evidence[0])
            prepared.append((record, RelationExtraction.model_validate(record["candidate"])))
    terminal = []
    for failure in failures:
        if failure.get("terminal") and failure["input_sha256"] not in seen:
            terminal.append(failure); seen.add(failure["input_sha256"])
    return prepared, terminal


def apply(db_path: Path, prepared: list[tuple[dict, RelationExtraction]], failures: list[dict]) -> dict:
    db_module.DB_PATH = db_path
    before_accepted = 0
    with sqlite3.connect(db_path) as db:
        before_accepted = db.execute("SELECT count(*) FROM relation_candidate WHERE review_status='accepted'").fetchone()[0]
    imported = failed = 0
    for record, candidate in prepared:
        evidence_id = record["evidence_span_id"]
        input_sha = sha256_text(candidate.model_dump_json())
        extraction_id = stable_id("extraction-job", "colab-local", record["model"], record["prompt_sha256"], record["input_sha256"])
        with db_module.transaction() as db:
            db.execute(
                """INSERT INTO extraction_job
                   (extraction_job_id,provider,model,prompt_sha256,input_sha256,
                    response_sha256,raw_response_json,status,review_status,created_at,completed_at)
                   VALUES (?, 'colab-local', ?, ?, ?, ?, ?, 'needs_review', 'needs_review', ?, ?)
                   ON CONFLICT(extraction_job_id) DO UPDATE SET
                     response_sha256=excluded.response_sha256,
                     raw_response_json=excluded.raw_response_json,
                     status='needs_review',review_status='needs_review',completed_at=excluded.completed_at""",
                (extraction_id, record["model"], record["prompt_sha256"], record["input_sha256"], input_sha,
                 json.dumps({"candidate": candidate.model_dump(), "checkpoint_record": record}, ensure_ascii=False),
                 record["created_at"], datetime.now(UTC).isoformat()),
            )
        validation = persist_candidate(evidence_id, extraction_id, candidate)
        with db_module.transaction() as db:
            row = db.execute("SELECT result_json FROM pipeline_job WHERE pipeline_job_id=?", (record["pipeline_job_id"],)).fetchone()
            payload = json.loads(row[0] or "{}") if row else {}
            payload["result"] = {"provider": "colab-local", "model": record["model"], "review_status": "needs_review", "validation_counts": validation}
            db.execute("UPDATE pipeline_job SET status='succeeded',completed_at=?,error_text=NULL,result_json=? WHERE pipeline_job_id=?",
                       (datetime.now(UTC).isoformat(), json.dumps(payload, sort_keys=True), record["pipeline_job_id"]))
        imported += 1
    with db_module.transaction() as db:
        for record in failures:
            db.execute("UPDATE pipeline_job SET status='failed',completed_at=?,error_text=? WHERE pipeline_job_id=? AND status<>'succeeded'",
                       (datetime.now(UTC).isoformat(), f"Colab terminal failure: {record.get('error','unknown')[:1500]}", record["pipeline_job_id"]))
            failed += db.total_changes
    with sqlite3.connect(db_path) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        after_accepted = db.execute("SELECT count(*) FROM relation_candidate WHERE review_status='accepted'").fetchone()[0]
    if integrity != "ok" or after_accepted != before_accepted:
        raise RuntimeError(f"post_import_guard_failed:integrity={integrity}:accepted={before_accepted}->{after_accepted}")
    return {"imported": imported, "terminal_failures": len(failures), "accepted_relations": after_accepted, "integrity": integrity}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "curated" / "rxn2-provisional.sqlite")
    parser.add_argument("--run-root", type=Path, default=Path(r"I:\My Drive\RXN2\relation-extraction\overnight-v2"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    prepared, failures = validate_all(args.db, args.run_root)
    report = {"validated_successes": len(prepared), "terminal_failures": len(failures), "applied": args.apply}
    if args.apply:
        report.update(apply(args.db, prepared, failures))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
