#!/usr/bin/env python3
"""Run RXN2's bounded, Drive-backed pipeline without a cloud scheduler."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without replacing process settings."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if not key.replace("_", "a").isalnum() or key[0].isdigit():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(ROOT / ".env")

from scripts.annotate_catalogue import annotate_catalogue
from scripts.build_pilot_queue import build_batch, write_queue
from scripts.bulk_pipeline import (
    DEFAULT_DB,
    DEFAULT_SCHEMA,
    connect,
    ingest_patent_candidates_jsonl,
    refresh_coverage,
)
from scripts.classify_patent_pdf import classify_pdf, write_report
from scripts.hybrid_storage import DEFAULT_POLICY, StoragePolicy, ensure_capacity
from scripts.ingest_ocr_result import ingest_ocr_result


CANDIDATE_NAMES = {
    "route_review_candidates.jsonl",
    "large_chemical_patent_candidates.jsonl",
    "candidates.jsonl",
}
MAX_COMPACT_IMPORT_BYTES = 512 * 1024 * 1024
MAX_TEXT_BYTES = 256 * 1024 * 1024


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(content, encoding="utf-8")
    partial.replace(path)


def job_id(job_type: str, identity: str) -> str:
    value = hashlib.sha256(f"{job_type}\0{identity}".encode()).hexdigest()[:24]
    return f"pipeline:{value}"


def run_job(
    db: sqlite3.Connection,
    job_type: str,
    identity: str,
    action: Callable[[], dict],
    input_sha256: str | None = None,
) -> dict:
    existing = db.execute(
        "SELECT status, result_json FROM pipeline_job WHERE job_type=? AND input_identity=?",
        (job_type, identity),
    ).fetchone()
    if existing and existing["status"] == "succeeded":
        return {
            "job_type": job_type,
            "status": "skipped",
            "reason": "already_succeeded",
            "result": json.loads(existing["result_json"] or "{}"),
        }
    timestamp = now()
    db.execute(
        """INSERT INTO pipeline_job
           (pipeline_job_id, job_type, input_identity, input_sha256, status,
            attempt_count, queued_at, started_at, result_json)
           VALUES (?, ?, ?, ?, 'running', 1, ?, ?, '{}')
           ON CONFLICT(job_type, input_identity) DO UPDATE SET
             input_sha256=excluded.input_sha256,
             status='running',
             attempt_count=pipeline_job.attempt_count+1,
             started_at=excluded.started_at,
             completed_at=NULL,
             error_text=NULL""",
        (job_id(job_type, identity), job_type, identity, input_sha256, timestamp, timestamp),
    )
    db.commit()
    try:
        result = action()
        db.execute(
            """UPDATE pipeline_job SET status='succeeded', completed_at=?,
               result_json=?, error_text=NULL WHERE job_type=? AND input_identity=?""",
            (now(), json.dumps(result, sort_keys=True), job_type, identity),
        )
        db.commit()
        return {"job_type": job_type, "status": "succeeded", "result": result}
    except Exception as error:
        db.rollback()
        db.execute(
            """UPDATE pipeline_job SET status='failed', completed_at=?, error_text=?
               WHERE job_type=? AND input_identity=?""",
            (now(), str(error)[:4000], job_type, identity),
        )
        db.commit()
        return {"job_type": job_type, "status": "failed", "error": str(error)}


def set_gate(db: sqlite3.Connection, name: str, configured: bool, detail: str) -> None:
    timestamp = now()
    db.execute(
        """INSERT INTO pipeline_job
           (pipeline_job_id, job_type, input_identity, status, attempt_count,
            queued_at, completed_at, result_json, error_text)
           VALUES (?, 'credential_gate', ?, ?, 0, ?, ?, '{}', ?)
           ON CONFLICT(job_type, input_identity) DO UPDATE SET
             status=excluded.status, completed_at=excluded.completed_at,
             error_text=excluded.error_text""",
        (
            job_id("credential_gate", name),
            name,
            "succeeded" if configured else "blocked",
            timestamp,
            timestamp,
            None if configured else detail,
        ),
    )
    db.commit()


def candidate_release(path: Path) -> str:
    release = path.parent.name
    suffix = "delta" if "large-chemical" in release else "ranked-local"
    return f"surechembl-{release}-{suffix}"


def extract_embedded_text(pdf: Path, output: Path, publication: str, document_sha: str) -> dict:
    try:
        import fitz
    except ModuleNotFoundError as error:
        raise RuntimeError("PyMuPDF is required; install requirements-pipeline.txt") from error
    pages = []
    chunks = []
    offset = 0
    with fitz.open(pdf) as document:
        for index, page in enumerate(document, 1):
            text = page.get_text("text")
            if chunks:
                offset += 2
            start = offset
            chunks.append(text)
            offset += len(text)
            pages.append(
                {
                    "page": index,
                    "char_start": start,
                    "char_end": offset,
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
            )
    combined = "\n\n".join(chunks).strip()
    if not combined:
        raise ValueError("embedded text extraction returned no text")
    if len(combined.encode()) > MAX_TEXT_BYTES:
        raise ValueError("embedded patent text exceeds the local processed-output limit")
    timestamp = now()
    run_id = f"embedded-{publication.casefold()}-{document_sha[:12]}"
    result = {
        "status": "succeeded",
        "job_id": run_id,
        "provider": "pymupdf",
        "model": "embedded-pdf-text-v1",
        "publication_number": publication,
        "source_document_sha256": document_sha,
        "created_at": timestamp,
        "completed_at": timestamp,
        "page_count": len(pages),
        "text": combined,
    }
    output.mkdir(parents=True, exist_ok=True)
    atomic_text(output / "result.txt", combined + "\n")
    atomic_text(output / "result.md", combined + "\n")
    atomic_text(
        output / "pages.jsonl",
        "".join(json.dumps(page, sort_keys=True) + "\n" for page in pages),
    )
    atomic_text(output / "result.json", json.dumps(result, indent=2, sort_keys=True) + "\n")
    return {"job_id": run_id, "pages": len(pages), "characters": len(combined)}


def write_ocr_queue(path: Path, publication: str, pdf: Path, document_sha: str) -> dict:
    record = {
        "publication_number": publication,
        "source_document": str(pdf),
        "source_document_sha256": document_sha,
        "status": "queued",
        "next_action": "run the Drive-backed Colab OCR notebook",
        "review_status": "unreviewed",
        "queued_at": now(),
    }
    atomic_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def complete_ocr_queue(path: Path, result_path: Path) -> None:
    if not path.is_file():
        return
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update({"status": "completed", "next_action": "none", "completed_result": str(result_path)})
    atomic_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n")


def database_summary(db: sqlite3.Connection) -> dict:
    tables = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    counts = {}
    for table in ("drug_entity", "compound", "patent_candidate", "extraction_job", "process_route"):
        counts[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    counts["elements"] = db.execute("SELECT count(*) FROM element").fetchone()[0]
    counts["structured_compounds"] = db.execute(
        "SELECT count(*) FROM compound WHERE smiles IS NOT NULL AND trim(smiles) <> ''"
    ).fetchone()[0]
    counts["annotated_compounds"] = db.execute("SELECT count(*) FROM compound_property").fetchone()[0]
    counts["tables"] = len(tables)
    return counts


def write_summary(db: sqlite3.Connection, drive_root: Path, results: list[dict]) -> dict:
    jobs = {
        row["status"]: row["count"]
        for row in db.execute("SELECT status, count(*) count FROM pipeline_job GROUP BY status")
    }
    exceptions = [
        dict(row)
        for row in db.execute(
            """SELECT pipeline_job_id, job_type, input_identity, status, error_text,
                      attempt_count, completed_at
               FROM pipeline_job WHERE status IN ('failed', 'blocked')
               ORDER BY completed_at DESC LIMIT 100"""
        )
    ]
    report = {
        "generated_at": now(),
        "mode": "windows-drive-colab",
        "database": database_summary(db),
        "job_statuses": jobs,
        "run_results": results,
        "exceptions": exceptions,
        "automatic_acceptance": False,
    }
    markdown = [
        "# RXN2 local automation status",
        "",
        f"Generated: {report['generated_at']}",
        "",
        f"Drugs: {report['database']['drug_entity']}",
        f"Compounds: {report['database']['compound']}",
        f"Patent candidates: {report['database']['patent_candidate']}",
        f"Elements: {report['database']['elements']}/118",
        f"Exceptions: {len(exceptions)}",
        "",
        "Machine extraction remains unreviewed; no route is accepted automatically.",
    ]
    for directory in (ROOT / "data" / "automation", drive_root / "reports"):
        atomic_text(directory / "automation-latest.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
        atomic_text(directory / "automation-latest.md", "\n".join(markdown) + "\n")
    return report


@contextmanager
def single_instance(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists() and datetime.now().timestamp() - lock_path.stat().st_mtime > 12 * 3600:
        lock_path.unlink()
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError("another RXN2 automation run is active") from error
    try:
        os.write(descriptor, f"pid={os.getpid()} started={now()}\n".encode())
        os.close(descriptor)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def run_pipeline(db_path: Path, schema: Path, policy_path: Path, drive_root: Path) -> tuple[dict, int]:
    policy = StoragePolicy.load(policy_path)
    policy.require_raw_root()
    ensure_capacity(policy, 0)
    if not drive_root.is_dir():
        raise RuntimeError(f"Google Drive RXN2 root is unavailable: {drive_root}")
    db = connect(db_path.resolve(), schema.resolve())
    results: list[dict] = []
    try:
        set_gate(db, "uspto_odp", bool(os.getenv("USPTO_ODP_API_KEY")), "USPTO_ODP_API_KEY is not configured")
        epo_ready = bool(os.getenv("EPO_OPS_CONSUMER_KEY") and os.getenv("EPO_OPS_CONSUMER_SECRET"))
        set_gate(db, "epo_ops", epo_ready, "EPO OPS credentials are not configured")

        processed = drive_root / "data" / "processed" / "surechembl"
        for path in sorted(processed.glob("*/*.jsonl")) if processed.is_dir() else []:
            if path.name not in CANDIDATE_NAMES or path.name.endswith(".partial"):
                continue
            if path.stat().st_size > MAX_COMPACT_IMPORT_BYTES:
                results.append({"job_type": "candidate_import", "status": "blocked", "error": f"compact import limit exceeded: {path}"})
                continue
            digest = sha256_file(path)
            results.append(
                run_job(
                    db,
                    "candidate_import",
                    f"{path}:{digest}",
                    lambda p=path: ingest_patent_candidates_jsonl(db, p, candidate_release(p)),
                    digest,
                )
            )

        counts = database_summary(db)
        if counts["elements"] < 118 or counts["annotated_compounds"] < counts["structured_compounds"]:
            identity = f"{counts['compound']}:{counts['structured_compounds']}:{counts['annotated_compounds']}:{counts['elements']}"
            results.append(run_job(db, "catalogue_annotation", identity, lambda: annotate_catalogue(db)))

        queue_identity = str(db.execute("SELECT count(*) FROM patent_candidate").fetchone()[0])
        queue_output = drive_root / "data" / "processed" / "pilot" / "route-review-batch-auto.jsonl"
        results.append(
            run_job(
                db,
                "review_queue",
                queue_identity,
                lambda: _build_queue(db, queue_output),
            )
        )

        raw_patents = drive_root / "patents" / "raw"
        for pdf in sorted(raw_patents.glob("*/*.pdf")) if raw_patents.is_dir() else []:
            publication = pdf.parent.name.replace(" ", "").upper()
            digest = sha256_file(pdf)
            routing_path = pdf.parent / "extraction-routing.json"
            results.append(
                run_job(
                    db,
                    "pdf_classification",
                    f"{publication}:{digest}",
                    lambda p=pdf, r=routing_path, n=publication: _classify(p, r, n),
                    digest,
                )
            )
            routing = json.loads(routing_path.read_text(encoding="utf-8"))
            if routing["source_document_sha256"] != digest:
                raise RuntimeError(f"stale PDF classification: {publication}")
            if routing["extraction_mode"] == "text_extraction":
                output = drive_root / "patents" / "text-results" / publication
                results.append(
                    run_job(
                        db,
                        "embedded_text_extraction",
                        f"{publication}:{digest}",
                        lambda p=pdf, o=output, n=publication, d=digest: extract_embedded_text(p, o, n, d),
                        digest,
                    )
                )
                result_path = output / "result.json"
                if result_path.is_file():
                    results.append(
                        run_job(
                            db,
                            "extraction_import",
                            f"pymupdf:{publication}:{sha256_file(result_path)}",
                            lambda rp=result_path, p=pdf, n=publication, d=digest: ingest_ocr_result(
                                db, rp, n, p, processed_root=ROOT / "data" / "processed" / "ocr",
                                provider="pymupdf", model="embedded-pdf-text-v1", source_document_sha256=d,
                            ),
                        )
                    )
            else:
                queue_path = drive_root / "patents" / "ocr-queue" / f"{publication}.json"
                results.append(
                    run_job(
                        db,
                        "ocr_queue",
                        f"{publication}:{digest}",
                        lambda q=queue_path, n=publication, p=pdf, d=digest: write_ocr_queue(q, n, p, d),
                        digest,
                    )
                )

        ocr_results = drive_root / "patents" / "ocr-results"
        for result_path in sorted(ocr_results.glob("*/result.json")) if ocr_results.is_dir() else []:
            publication = result_path.parent.name.replace(" ", "").upper()
            source_pdf = raw_patents / publication / f"{publication}.pdf"
            digest = sha256_file(result_path)
            import_result = run_job(
                    db,
                    "extraction_import",
                    f"ocr:{publication}:{digest}",
                    lambda rp=result_path, p=source_pdf, n=publication: ingest_ocr_result(
                        db, rp, n, p, processed_root=ROOT / "data" / "processed" / "ocr"
                    ),
                    digest,
                )
            results.append(import_result)
            if import_result["status"] != "failed":
                complete_ocr_queue(drive_root / "patents" / "ocr-queue" / f"{publication}.json", result_path)

        day = datetime.now(UTC).date().isoformat()
        results.append(run_job(db, "coverage_refresh", day, lambda: refresh_coverage(db)))
        results.append(run_job(db, "integrity_check", day, lambda: _integrity(db)))
        report = write_summary(db, drive_root, results)
        return report, int(any(item["status"] == "failed" for item in results))
    finally:
        db.close()


def _classify(pdf: Path, output: Path, publication: str) -> dict:
    result = classify_pdf(pdf, publication)
    write_report(result, output)
    return result


def _build_queue(db: sqlite3.Connection, output: Path) -> dict:
    queue = build_batch(db, 50)
    write_queue(queue, output)
    return {"drugs": len(queue), "families": len({row["family_id"] for row in queue}), "output": str(output)}


def _integrity(db: sqlite3.Connection) -> dict:
    result = db.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {result}")
    return {"integrity": result}


def print_status(db_path: Path, schema: Path) -> int:
    db = connect(db_path.resolve(), schema.resolve())
    try:
        payload = {
            "database": database_summary(db),
            "jobs": [dict(row) for row in db.execute(
                "SELECT * FROM pipeline_job ORDER BY coalesce(completed_at, started_at, queued_at) DESC LIMIT 100"
            )],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    finally:
        db.close()


def retry_job(db_path: Path, schema: Path, identifier: str) -> int:
    db = connect(db_path.resolve(), schema.resolve())
    try:
        changed = db.execute(
            """UPDATE pipeline_job SET status='queued', error_text=NULL
               WHERE pipeline_job_id=? AND status IN ('failed', 'blocked')""",
            (identifier,),
        ).rowcount
        db.commit()
        print(json.dumps({"pipeline_job_id": identifier, "queued": bool(changed)}))
        return 0 if changed else 1
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument(
        "--drive-root",
        type=Path,
        default=Path(os.getenv("RXN2_DRIVE_ROOT", r"I:\My Drive\RXN2")),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    commands.add_parser("status")
    retry = commands.add_parser("retry")
    retry.add_argument("pipeline_job_id")
    args = parser.parse_args(argv)
    if args.command == "status":
        return print_status(args.db, args.schema)
    if args.command == "retry":
        return retry_job(args.db, args.schema, args.pipeline_job_id)
    lock = ROOT / "data" / "automation" / "local-automation.lock"
    try:
        with single_instance(lock):
            report, code = run_pipeline(
                args.db.resolve(), args.schema.resolve(), args.policy.resolve(), args.drive_root.resolve()
            )
        print(json.dumps(report, indent=2, sort_keys=True))
        return code
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
