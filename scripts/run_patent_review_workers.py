#!/usr/bin/env python3
"""Shard patent examples across separately authorized Groq project keys."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KEY_NAMES = ("GROQ_API_KEY", "q_api_key") + tuple(
    f"q_api_key{index}" for index in range(32)
)
DEFAULT_MODEL = "qwen/qwen3.6-27b"


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())


def configured_keys() -> list[tuple[str, str]]:
    seen = set()
    result = []
    for name in KEY_NAMES:
        value = os.getenv(name)
        if value and value not in seen:
            seen.add(value)
            result.append((name, value))
    return result


def read_records(path: Path) -> list[dict]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(f"Malformed JSONL at {path}:{line_number}") from error
    return records


def shard_records(records: list[dict], count: int) -> list[list[dict]]:
    if count < 1:
        raise ValueError("worker count must be positive")
    shards = [[] for _ in range(count)]
    for index, record in enumerate(records):
        shards[index % count].append(record)
    return shards


def interleave_publications(records: list[dict]) -> list[dict]:
    groups: dict[str, deque] = defaultdict(deque)
    order = []
    for record in records:
        publication = str(record.get("publication_number") or "")
        if publication not in groups:
            order.append(publication)
        groups[publication].append(record)
    result = []
    while any(groups[publication] for publication in order):
        for publication in order:
            if groups[publication]:
                result.append(groups[publication].popleft())
    return result


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    if path.is_file() and path.read_text(encoding="utf-8") != content:
        raise ValueError(f"Refusing to replace changed shard input: {path}")
    path.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--example-blocks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=0, help="0 uses every unique Groq key")
    parser.add_argument(
        "--limit-per-worker", type=int, default=1,
        help="pilot default is 1; use 0 only after reviewing pilot quality",
    )
    parser.add_argument(
        "--max-record-chars", type=int, default=12000,
        help="defer oversized examples instead of truncating evidence; 0 disables",
    )
    parser.add_argument("--max-output-tokens", type=int, default=3072)
    args = parser.parse_args()
    if (
        args.workers < 0 or args.limit_per_worker < 0
        or args.max_record_chars < 0 or args.max_output_tokens < 256
    ):
        raise SystemExit("workers, limits, and max-record-chars cannot be negative")
    load_env(args.env_file)
    keys = configured_keys()
    if args.workers:
        keys = keys[: args.workers]
    if not keys:
        raise SystemExit("No authorized Groq project keys are configured")

    all_records = read_records(args.example_blocks)
    records = [
        record for record in all_records
        if not args.max_record_chars or len(record.get("text", "")) <= args.max_record_chars
    ]
    deferred = len(all_records) - len(records)
    records = interleave_publications(records)
    shards = shard_records(records, len(keys))
    children = []
    for index, ((key_name, key), shard) in enumerate(zip(keys, shards), 1):
        worker_root = args.output / f"worker-{index:02d}"
        input_path = worker_root / "input.jsonl"
        write_jsonl(input_path, shard)
        environment = os.environ.copy()
        for name in KEY_NAMES:
            environment.pop(name, None)
        environment["GROQ_API_KEY"] = key
        environment["PATENT_REVIEW_MAX_OUTPUT_TOKENS"] = str(args.max_output_tokens)
        command = [
            sys.executable,
            "scripts/review_patent_examples_with_llm.py",
            "--provider", "groq",
            "--model", args.model,
            "--example-blocks", str(input_path),
            "--output", str(worker_root / "results"),
            "--limit", str(args.limit_per_worker),
        ]
        stdout = (worker_root / "worker.log").open("a", encoding="utf-8")
        stderr = (worker_root / "worker.err.log").open("a", encoding="utf-8")
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr
        )
        children.append((index, key_name, process, stdout, stderr))
        print(f"started Groq patent worker {index} for authorized project {key_name}; pid={process.pid}")

    statuses = []
    for index, key_name, process, stdout, stderr in children:
        returncode = process.wait()
        stdout.close()
        stderr.close()
        statuses.append({"worker": index, "key_name": key_name, "returncode": returncode})
    manifest = {
        "schema_version": "rxn2-patent-review-workers-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "provider": "groq",
        "model": args.model,
        "configured_unique_keys": len(keys),
        "input_records": len(all_records),
        "eligible_records": len(records),
        "deferred_oversized_records": deferred,
        "max_record_chars": args.max_record_chars,
        "max_output_tokens": args.max_output_tokens,
        "input_sha256": hashlib.sha256(args.example_blocks.read_bytes()).hexdigest(),
        "limit_per_worker": args.limit_per_worker,
        "workers": statuses,
        "safety": {
            "distinct_input_shards": True,
            "rotates_keys_for_same_request": False,
            "accepts_gold_chemistry": False,
            "human_review_required": True,
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 1 if any(item["returncode"] for item in statuses) else 0


if __name__ == "__main__":
    raise SystemExit(main())
