#!/usr/bin/env python3
"""Run RXN2 public-patent relation extraction slowly and resumably on one API key.

This worker deliberately does not rotate API keys. It respects provider limits by
pausing after transient failures, then retries the same queued work later.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-provisional.sqlite"
DEFAULT_OUTPUT = Path("I:/My Drive/RXN2/data/processed/epo_ops/provisional-relation-graph-2026-08-22-v1")
TRANSIENT_MARKERS = ("429", "rate limit", "timeout", "timed out", "503", "temporarily", "invalid json", "eof while parsing", "model is busy", "completion_error", "unprocessable entity")


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())


def counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as db:
        return dict(db.execute(
            "SELECT status, count(*) FROM pipeline_job "
            "WHERE job_type='relation_extraction' GROUP BY status"
        ))


def latest_failure(db_path: Path) -> str:
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            """SELECT error_text FROM pipeline_job
               WHERE job_type='relation_extraction' AND status='failed'
               ORDER BY completed_at DESC LIMIT 1"""
        ).fetchone()
    return row[0] if row and row[0] else ""


def requeue_transient_failures(db_path: Path) -> int:
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            """SELECT pipeline_job_id, error_text FROM pipeline_job
               WHERE job_type='relation_extraction' AND status='failed'"""
        ).fetchall()
        retry = [job_id for job_id, error in rows if any(
            marker in (error or "").casefold() for marker in TRANSIENT_MARKERS
        )]
        for job_id in retry:
            db.execute(
                """UPDATE pipeline_job SET status='queued', started_at=NULL,
                   completed_at=NULL, error_text='transient provider failure; delayed retry'
                   WHERE pipeline_job_id=?""",
                (job_id,),
            )
        db.commit()
    return len(retry)


def run_once(args: argparse.Namespace) -> int:
    command = [
        sys.executable, "scripts/build_relation_graph.py", "--env-file", str(args.env_file),
        "run", "--db", str(args.db), "--output-dir", str(args.output_dir),
        "--limit", "1", "--concurrency", "1", "--no-export", "--provider", args.provider,
    ]
    env = os.environ.copy()
    # One known-working free fallback. Change only after the provider has capacity.
    if args.provider == "groq":
        env.setdefault("RELATION_GROQ_MODEL", "openai/gpt-oss-20b")
    elif args.provider == "huggingface":
        env.setdefault("RELATION_HF_MODEL", "Qwen/Qwen3-32B:featherless-ai")
    else:
        env.setdefault("RELATION_OPENROUTER_MODELS", "nvidia/nemotron-3-ultra-550b-a55b:free")
    env.setdefault("RELATION_TIMEOUT_SECONDS", "60")
    env.setdefault("RELATION_MAX_OUTPUT_TOKENS", "2048")
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--idle-seconds", type=int, default=30)
    parser.add_argument("--max-jobs", type=int, default=0, help="0 means until queue completion")
    parser.add_argument("--allow-parallel", action="store_true", help="claim distinct jobs alongside approved shard workers")
    parser.add_argument("--provider", choices=("openrouter", "groq", "huggingface"), default="openrouter")
    args = parser.parse_args()
    load_env(args.env_file)
    configured = (
        bool(os.getenv("GROQ_API_KEY")) if args.provider == "groq"
        else bool(os.getenv("HF_TOKEN")) if args.provider == "huggingface"
        else bool(os.getenv("OPENROUTER_API_KEY") or os.getenv("op_api_key"))
    )
    if not configured:
        raise SystemExit(f"Configure one {args.provider} key before running this worker.")

    completed = 0
    delay = max(args.idle_seconds, 10)
    while args.max_jobs == 0 or completed < args.max_jobs:
        state = counts(args.db)
        if state.get("running", 0) and not args.allow_parallel:
            print(f"{datetime.now(UTC).isoformat()} another batch is active; waiting")
            time.sleep(args.idle_seconds)
            continue
        if state.get("queued", 0) == 0:
            print(f"{datetime.now(UTC).isoformat()} queue complete: {state}")
            return

        before = state.get("succeeded", 0)
        returncode = run_once(args)
        after = counts(args.db)
        if after.get("succeeded", 0) > before:
            completed += 1
            delay = max(args.idle_seconds, 10)
            print(f"{datetime.now(UTC).isoformat()} completed={completed} queue={after.get('queued', 0)}")
            continue

        error = latest_failure(args.db)
        retried = requeue_transient_failures(args.db)
        if retried or returncode:
            print(f"{datetime.now(UTC).isoformat()} delayed retry in {delay}s: {error[:240]}")
            time.sleep(delay)
            delay = min(delay * 2, 3600)
            continue

        # A non-transient rejected procedure stays accounted for; move forward.
        print(f"{datetime.now(UTC).isoformat()} no completion; continuing: {after}")
        time.sleep(args.idle_seconds)


if __name__ == "__main__":
    main()
