#!/usr/bin/env python3
"""Show RXN2 relation-extraction worker progress; add --watch for live refresh."""
from __future__ import annotations

import argparse
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "curated" / "rxn2-provisional.sqlite"
LOG_DIR = ROOT / "data" / "processed"


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def tail(path: Path, lines: int = 2) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]


def show() -> None:
    with sqlite3.connect(DB) as db:
        counts = dict(db.execute(
            "SELECT status, count(*) FROM pipeline_job "
            "WHERE job_type='relation_extraction' GROUP BY status"
        ))
        total = sum(counts.values())
        completed = counts.get("succeeded", 0)
        print("RXN2 public-patent relation extraction")
        print(f"Completed: {completed}/{total}  |  Running: {counts.get('running', 0)}  |  Queued: {counts.get('queued', 0)}  |  Failed: {counts.get('failed', 0)}")
        if total:
            print(f"Progress: {completed / total:.1%}")
    now = time.time()
    for label, pattern in (
        ("OpenRouter", "free-relation-worker-*.log"),
        ("Groq", "groq-relation-worker-*.log"),
        ("Hugging Face", "huggingface-relation-worker-*.log"),
    ):
        logs = sorted(
            (path for path in LOG_DIR.glob(pattern) if not path.name.endswith(".err.log")),
            key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
        )
        for log in logs:
            index = int(log.stem.rsplit("-", 1)[-1])
            age = max(0, int(now - log.stat().st_mtime))
            state = "active" if age < 90 else "rate-limit backoff"
            print(f"{label} worker {index}: {state}; last activity {age}s ago")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--seconds", type=int, default=15)
    args = parser.parse_args()
    while True:
        clear()
        show()
        if not args.watch:
            return
        print(f"\nRefreshing every {args.seconds} seconds. Press Ctrl+C to stop viewing.")
        time.sleep(args.seconds)


if __name__ == "__main__":
    main()
