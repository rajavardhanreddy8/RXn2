#!/usr/bin/env python3
"""Run queued production patent-relation jobs, one at a time and resumably.

This runner intentionally uses one configured provider key.  It writes only
``needs_review`` or rejected relation candidates through the existing core
validation path; it cannot accept reactions or routes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import db as db_module
from apps.api.app.relations import process_evidence_span
from scripts.hybrid_storage import DEFAULT_POLICY, StoragePolicy, ensure_capacity


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def assert_storage_ready(policy_path: Path = DEFAULT_POLICY) -> None:
    """Fail before claiming jobs if local state cannot be written safely."""
    policy = StoragePolicy.load(policy_path)
    policy.require_raw_root()
    ensure_capacity(policy, 0)


def claim(db_path: Path) -> dict | None:
    with sqlite3.connect(db_path, timeout=90) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=90000")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """SELECT pipeline_job_id, result_json FROM pipeline_job
               WHERE job_type='relation_extraction' AND status='queued'
               ORDER BY attempt_count, queued_at LIMIT 1"""
        ).fetchone()
        if not row:
            db.commit()
            return None
        db.execute(
            """UPDATE pipeline_job
               SET status='running', started_at=?, attempt_count=attempt_count+1
               WHERE pipeline_job_id=? AND status='queued'""",
            (datetime.now(UTC).isoformat(), row["pipeline_job_id"]),
        )
        db.commit()
        return dict(row)


def finish(db_path: Path, job_id: str, payload: dict, status: str, error: str | None) -> None:
    with sqlite3.connect(db_path, timeout=90) as db:
        db.execute("PRAGMA busy_timeout=90000")
        db.execute(
            """UPDATE pipeline_job SET status=?, completed_at=?, result_json=?, error_text=?
               WHERE pipeline_job_id=?""",
            (status, datetime.now(UTC).isoformat(), json.dumps(payload), error, job_id),
        )
        db.commit()


def state(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as db:
        return dict(
            db.execute(
                """SELECT status, count(*) FROM pipeline_job
                   WHERE job_type='relation_extraction' GROUP BY status"""
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data/curated/rxn2-production.sqlite")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--storage-policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--provider", choices=("openrouter", "groq"), default="openrouter")
    parser.add_argument("--model", required=True, help="The explicitly selected provider model.")
    parser.add_argument("--max-jobs", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--pause-seconds", type=float, default=2)
    args = parser.parse_args()
    load_env(args.env_file)
    assert_storage_ready(args.storage_policy)
    db_module.DB_PATH = args.db
    if args.provider == "openrouter" and not (os.getenv("OPENROUTER_API_KEY") or os.getenv("op_api_key")):
        raise SystemExit("OpenRouter is not configured in the env file.")
    if args.provider == "groq" and not (os.getenv("GROQ_API_KEY") or os.getenv("q_api_key")):
        raise SystemExit("Groq is not configured in the env file.")
    if args.provider == "openrouter" and not args.model.endswith(":free"):
        raise SystemExit("Only explicitly free OpenRouter models are allowed.")
    if not 1 <= args.concurrency <= 8:
        raise SystemExit("Concurrency must be between 1 and 8.")

    async def execute(job: dict) -> bool:
        payload = json.loads(job["result_json"])
        try:
            result = await process_evidence_span(
                payload["evidence_span_id"], provider=args.provider, model=args.model
            )
            payload["result"] = result
            finish(args.db, job["pipeline_job_id"], payload, "succeeded", None)
            return True
        except Exception as error:
            # Preserve the failure for diagnosis.  A later explicit retry may requeue it.
            finish(args.db, job["pipeline_job_id"], payload, "failed", str(error))
            print(json.dumps({"failed": job["pipeline_job_id"], "error": str(error)[:500]}))
            return False

    async def run() -> None:
        attempted = 0
        succeeded = 0
        while attempted < args.max_jobs:
            jobs = []
            while len(jobs) < args.concurrency and attempted + len(jobs) < args.max_jobs:
                job = claim(args.db)
                if not job:
                    break
                jobs.append(job)
            if not jobs:
                print(json.dumps({"state": "queue_empty", "counts": state(args.db)}))
                return
            outcomes = await asyncio.gather(*(execute(job) for job in jobs))
            attempted += len(jobs)
            succeeded += sum(outcomes)
            print(json.dumps({"attempted": attempted, "succeeded": succeeded, "counts": state(args.db)}))
            if attempted < args.max_jobs:
                await asyncio.sleep(max(0, args.pause_seconds))

    asyncio.run(run())


if __name__ == "__main__":
    main()
