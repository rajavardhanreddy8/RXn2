#!/usr/bin/env python3
"""Retry only explicitly rate-limited production relation jobs once.

The script never rotates credentials or retries semantic/schema failures.  It
is intended for one scheduled provider cooldown retry followed by an optional
rebuild of the derived graph projection.
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"


def requeue_rate_limited() -> int:
    with sqlite3.connect(DB) as db:
        rows = db.execute(
            """SELECT pipeline_job_id, error_text FROM pipeline_job
               WHERE job_type='relation_extraction' AND status='failed'"""
        ).fetchall()
        retry = [
            job_id for job_id, error in rows
            if "429 too many requests" in (error or "").casefold()
        ]
        for job_id in retry:
            db.execute(
                """UPDATE pipeline_job SET status='queued', started_at=NULL,
                   completed_at=NULL, error_text='one delayed retry after transient Groq rate limit'
                   WHERE pipeline_job_id=?""",
                (job_id,),
            )
        db.commit()
    return len(retry)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay-seconds", type=float, default=0)
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    args = parser.parse_args()
    time.sleep(max(0, args.delay_seconds))
    queued = requeue_rate_limited()
    if not queued:
        print("no_rate_limited_relation_jobs")
        return
    subprocess.run(
        [
            sys.executable, "scripts/run_production_relation_queue.py",
            "--provider", "groq", "--model", args.model,
            "--max-jobs", str(queued),
        ],
        cwd=ROOT,
        check=False,
    )
    subprocess.run([sys.executable, "scripts/build_large_graph.py"], cwd=ROOT, check=False)
    print(f"retried_rate_limited_jobs={queued}")


if __name__ == "__main__":
    main()
