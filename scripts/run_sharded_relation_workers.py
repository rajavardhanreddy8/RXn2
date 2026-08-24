#!/usr/bin/env python3
"""Launch one rate-limit-aware RXN2 worker per separately authorized project key.

Each worker receives exactly one key. SQLite's immediate claim transaction assigns
non-overlapping queued procedures, so no procedure is submitted twice.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_NAMES = ("op_api_key",) + tuple(f"op_api_key{index}" for index in range(32))


def load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--workers", type=int, default=0, help="0 uses every unique configured project key")
    parser.add_argument("--idle-seconds", type=int, default=30)
    args = parser.parse_args()
    load_env(args.env_file)
    configured = [(name, os.getenv(name)) for name in KEY_NAMES if os.getenv(name)]
    # Avoid duplicate configuration accidentally creating duplicate project workers.
    unique = []
    seen = set()
    for name, key in configured:
        if key not in seen:
            unique.append((name, key))
            seen.add(key)
    configured = unique[:args.workers] if args.workers > 0 else unique
    if not configured:
        raise SystemExit("No approved project keys configured.")

    log_dir = ROOT / "data" / "processed"
    log_dir.mkdir(parents=True, exist_ok=True)
    model_chains = (
        "z-ai/glm-5.2:free,nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free,z-ai/glm-5.2:free",
        "google/gemma-4-31b-it:free,z-ai/glm-5.2:free,nvidia/nemotron-3-ultra-550b-a55b:free",
        "z-ai/glm-5.2:free,google/gemma-4-31b-it:free,nvidia/nemotron-3-ultra-550b-a55b:free",
    )
    children = []
    for index, (name, key) in enumerate(configured, 1):
        environment = os.environ.copy()
        for key_name in KEY_NAMES:
            environment.pop(key_name, None)
        environment["OPENROUTER_API_KEY"] = key
        environment["RELATION_OPENROUTER_MODELS"] = model_chains[(index - 1) % len(model_chains)]
        environment["RELATION_TIMEOUT_SECONDS"] = "60"
        environment["RELATION_MAX_OUTPUT_TOKENS"] = "2048"
        stdout = (log_dir / f"free-relation-worker-{index}.log").open("a", encoding="utf-8")
        stderr = (log_dir / f"free-relation-worker-{index}.err.log").open("a", encoding="utf-8")
        child = subprocess.Popen(
            [sys.executable, "scripts/run_free_relation_worker.py", "--allow-parallel",
             "--idle-seconds", str(args.idle_seconds)],
            cwd=ROOT, env=environment, stdout=stdout, stderr=stderr,
        )
        children.append(child)
        print(f"started worker {index} for approved project {name}; pid={child.pid}")
    for child in children:
        child.wait()


if __name__ == "__main__":
    main()
