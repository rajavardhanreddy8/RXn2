#!/usr/bin/env python3
"""Launch one RXN2 queue worker per separately authorized Groq project key."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_NAMES = ("q_api_key",) + tuple(f"q_api_key{index}" for index in range(32))


def load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--workers", type=int, default=0, help="0 uses every unique configured Groq project key")
    parser.add_argument("--skip", type=int, default=0, help="Skip the first N unique configured keys")
    parser.add_argument("--idle-seconds", type=int, default=30)
    args = parser.parse_args()
    load_env(args.env_file)
    unique = []
    seen = set()
    for name in KEY_NAMES:
        key = os.getenv(name)
        if key and key not in seen:
            unique.append((name, key))
            seen.add(key)
    available = unique[args.skip:]
    configured = available[:args.workers] if args.workers > 0 else available
    if not configured:
        raise SystemExit("No approved Groq project keys configured.")

    log_dir = ROOT / "data" / "processed"
    log_dir.mkdir(parents=True, exist_ok=True)
    children = []
    for index, (name, key) in enumerate(configured, args.skip + 1):
        environment = os.environ.copy()
        for key_name in KEY_NAMES:
            environment.pop(key_name, None)
        environment["GROQ_API_KEY"] = key
        environment["RELATION_GROQ_MODEL"] = "openai/gpt-oss-20b"
        environment["RELATION_TIMEOUT_SECONDS"] = "60"
        environment["RELATION_MAX_OUTPUT_TOKENS"] = "2048"
        stdout = (log_dir / f"groq-relation-worker-{index}.log").open("a", encoding="utf-8")
        stderr = (log_dir / f"groq-relation-worker-{index}.err.log").open("a", encoding="utf-8")
        child = subprocess.Popen(
            [sys.executable, "scripts/run_free_relation_worker.py", "--allow-parallel",
             "--provider", "groq", "--idle-seconds", str(args.idle_seconds)],
            cwd=ROOT, env=environment, stdout=stdout, stderr=stderr,
        )
        children.append(child)
        print(f"started Groq worker {index} for approved project {name}; pid={child.pid}")
    for child in children:
        child.wait()


if __name__ == "__main__":
    main()
