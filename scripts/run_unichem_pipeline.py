#!/usr/bin/env python3
"""Run resumable UniChem acquisition, conversion, and atomic catalogue ingestion."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVE = Path("I:/My Drive/RXN2")
RAW_DIR = DRIVE / "data" / "raw" / "unichem" / "2026-08-22"
PROCESSED = DRIVE / "data" / "processed" / "unichem" / "2026-08-22"
STATUS = PROCESSED / "pipeline-status.json"
DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"


def write_status(stage: str, status: str, **extra: object) -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    payload = {"stage": stage, "status": status, "updated_at": datetime.now(UTC).isoformat(), **extra}
    partial = STATUS.with_suffix(".json.partial")
    partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(STATUS)


def run(stage: str, command: list[str], attempts: int = 6) -> None:
    for attempt in range(1, attempts + 1):
        write_status(stage, "running", attempt=attempt)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode == 0:
            write_status(stage, "succeeded", attempt=attempt)
            return
        if attempt == attempts:
            write_status(stage, "failed", attempt=attempt, returncode=result.returncode)
            raise SystemExit(result.returncode)
        delay = min(60 * (2 ** (attempt - 1)), 1800)
        write_status(stage, "retry_wait", attempt=attempt, delay_seconds=delay)
        time.sleep(delay)


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    mappings = PROCESSED / "mappings.jsonl"
    catalogue = PROCESSED / "catalogue.jsonl"
    run("acquire", [python, "scripts/fetch_unichem_seeded.py", "--db", str(DB),
        "--raw", str(RAW_DIR / "compounds.jsonl"), "--output", str(mappings),
        "--report", str(RAW_DIR / "manifest.json"), "--sleep-seconds", "0.25"])
    run("convert", [python, "scripts/catalogue_converters.py", "unichem",
        "--input", str(mappings), "--output", str(catalogue),
        "--report", str(PROCESSED / "reconciliation.json")])
    run("ingest", [python, "scripts/bulk_pipeline.py", "--db", str(DB),
        "ingest-catalogue-jsonl", "--input", str(catalogue),
        "--source", "unichem_bulk", "--release", "2026-08-22"])
    run("coverage", [python, "scripts/bulk_pipeline.py", "--db", str(DB), "refresh-coverage"])
    write_status("complete", "succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())