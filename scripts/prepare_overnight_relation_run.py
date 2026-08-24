#!/usr/bin/env python3
"""Create an atomic, schema-pinned RXN2 Colab overnight run bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.colab_relation_common import (
    MODEL_NAME, PROMPT_SHA256, RELATION_SCHEMA, SCHEMA_VERSION, SYSTEM_PROMPT, job_hash,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(value, encoding="utf-8")
    os.replace(partial, path)


def prepare(db_path: Path, run_root: Path, maximum_chars: int) -> dict:
    query = """
        SELECT p.pipeline_job_id,p.result_json,e.evidence_span_id,
               e.publication_number,e.evidence_text,e.source_url
        FROM pipeline_job p JOIN evidence_span e
          ON e.evidence_span_id=json_extract(p.result_json,'$.evidence_span_id')
        WHERE p.job_type='relation_extraction' AND p.status='queued'
        ORDER BY CASE json_extract(p.result_json,'$.candidate_status')
                   WHEN 'participant_roles_partial' THEN 0 ELSE 1 END,
                 p.attempt_count,length(e.evidence_text),p.queued_at
    """
    jobs = []
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute(query):
            if len(row["evidence_text"]) > maximum_chars:
                raise RuntimeError(
                    f"Oversized queued evidence remains: {row['evidence_span_id']} "
                    f"({len(row['evidence_text'])} chars). Run split_oversized_relation_jobs.py --apply."
                )
            payload = json.loads(row["result_json"] or "{}")
            job = {
                "pipeline_job_id": row["pipeline_job_id"],
                "evidence_span_id": row["evidence_span_id"],
                "publication_number": row["publication_number"],
                "evidence_text": row["evidence_text"],
                "source_url": row["source_url"],
                "candidate_status": payload.get("candidate_status", "evidence_only"),
                "parent_evidence_span_id": payload.get("parent_evidence_span_id"),
                "chunk_index": payload.get("chunk_index"),
                "chunk_count": payload.get("chunk_count"),
            }
            job["input_sha256"] = job_hash(job)
            jobs.append(job)
    input_path = run_root / "jobs" / "jobs.jsonl"
    schema_path = run_root / "jobs" / "relation-schema.json"
    prompt_path = run_root / "jobs" / "relation-prompt.txt"
    runtime_sources = {
        "colab_relation_common.py": Path(__file__).with_name("colab_relation_common.py"),
        "colab_overnight_runner.py": Path(__file__).with_name("colab_overnight_runner.py"),
    }
    atomic_text(input_path, "".join(json.dumps(job, ensure_ascii=False) + "\n" for job in jobs))
    atomic_text(schema_path, json.dumps(RELATION_SCHEMA, ensure_ascii=False, indent=2, sort_keys=True))
    atomic_text(prompt_path, SYSTEM_PROMPT)
    runtime_paths = {}
    for name, source in runtime_sources.items():
        destination = run_root / "runner" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial_runtime = destination.with_suffix(".py.partial")
        shutil.copyfile(source, partial_runtime)
        os.replace(partial_runtime, destination)
        runtime_paths[name] = destination
    categories = Counter(job["candidate_status"] for job in jobs)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "model": MODEL_NAME,
        "prompt_sha256": PROMPT_SHA256,
        "records": len(jobs),
        "candidate_status_counts": dict(sorted(categories.items())),
        "maximum_evidence_chars": max((len(job["evidence_text"]) for job in jobs), default=0),
        "files": {
            "jobs/jobs.jsonl": sha256_file(input_path),
            "jobs/relation-schema.json": sha256_file(schema_path),
            "jobs/relation-prompt.txt": sha256_file(prompt_path),
            "runner/colab_relation_common.py": sha256_file(runtime_paths["colab_relation_common.py"]),
            "runner/colab_overnight_runner.py": sha256_file(runtime_paths["colab_overnight_runner.py"]),
        },
        "legacy_results": "../results/results.jsonl",
        "legacy_results_policy": "preserved_unvalidated_not_counted",
    }
    atomic_text(run_root / "jobs" / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/curated/rxn2-provisional.sqlite"))
    parser.add_argument("--run-root", type=Path, default=Path(r"I:\My Drive\RXN2\relation-extraction\overnight-v2"))
    parser.add_argument("--maximum-chars", type=int, default=12000)
    args = parser.parse_args()
    print(json.dumps(prepare(args.db, args.run_root, args.maximum_chars), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
