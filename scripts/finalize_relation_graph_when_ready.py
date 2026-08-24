#!/usr/bin/env python3
"""Wait for the relation queue, then export and integrity-check the provisional graph."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "curated" / "rxn2-provisional.sqlite"
OUTPUT = Path("I:/My Drive/RXN2/data/processed/epo_ops/provisional-relation-graph-2026-08-22-v1")
STATUS = OUTPUT / "finalizer-status.json"


def counts() -> dict[str, int]:
    with sqlite3.connect(DB) as db:
        return dict(db.execute("SELECT status,count(*) FROM pipeline_job WHERE job_type='relation_extraction' GROUP BY status"))


def write(status: str, queue: dict[str, int], **extra: object) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    payload = {"status": status, "updated_at": datetime.now(UTC).isoformat(), "queue": queue, **extra}
    partial = STATUS.with_suffix(".json.partial")
    partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(STATUS)


def main() -> int:
    while True:
        queue = counts()
        if queue.get("queued", 0) == 0 and queue.get("running", 0) == 0:
            break
        write("waiting", queue)
        time.sleep(60)
    with sqlite3.connect(DB) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        write("blocked", queue, integrity=integrity)
        return 1
    write("exporting", queue, integrity=integrity)
    result = subprocess.run([sys.executable, "scripts/build_relation_graph.py", "export",
        "--db", str(DB), "--output-dir", str(OUTPUT)], cwd=ROOT, check=False)
    write("succeeded" if result.returncode == 0 else "failed", counts(),
          integrity=integrity, export_returncode=result.returncode)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())