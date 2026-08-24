#!/usr/bin/env python3
"""Process RXN2 relation jobs through the private Hugging Face ZeroGPU Space."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import db as db_module
from apps.api.app.relations import (
    RelationExtraction,
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    persist_candidate,
    sha256_text,
    stable_id,
)

DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-provisional.sqlite"
DEFAULT_SPACE = "rajavr18/rxn2-relation-extractor"
DEFAULT_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
MAX_ITEM_CHARS = 15_000
MAX_BATCH_CHARS = 70_000


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=90)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=90000")
    return db


def queue_counts(path: Path) -> dict[str, int]:
    with connect(path) as db:
        return dict(db.execute(
            "SELECT status,count(*) FROM pipeline_job "
            "WHERE job_type='relation_extraction' GROUP BY status"
        ))


def claim_batch(path: Path, limit: int) -> list[dict]:
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            """SELECT p.pipeline_job_id,p.result_json,e.evidence_span_id,
                      e.evidence_text,e.source_url
               FROM pipeline_job p
               JOIN evidence_span e
                 ON e.evidence_span_id=json_extract(p.result_json,'$.evidence_span_id')
               WHERE p.job_type='relation_extraction' AND p.status='queued'
                 AND length(e.evidence_text)<=?
               ORDER BY CASE json_extract(p.result_json,'$.candidate_status')
                          WHEN 'participant_roles_partial' THEN 0 ELSE 1 END,
                        p.attempt_count,length(e.evidence_text),p.queued_at
               LIMIT ?""",
            (MAX_ITEM_CHARS, limit * 3),
        ).fetchall()
        selected, total = [], 0
        for row in rows:
            size = len(row["evidence_text"])
            if selected and total + size > MAX_BATCH_CHARS:
                continue
            selected.append(dict(row)); total += size
            if len(selected) >= limit:
                break
        now = datetime.now(UTC).isoformat()
        for row in selected:
            db.execute(
                """UPDATE pipeline_job SET status='running',started_at=?,
                          completed_at=NULL,attempt_count=attempt_count+1,error_text=NULL
                   WHERE pipeline_job_id=? AND status='queued'""",
                (now, row["pipeline_job_id"]),
            )
        db.commit()
    return selected


def requeue(path: Path, jobs: list[dict], error: str) -> None:
    with connect(path) as db:
        for job in jobs:
            db.execute(
                """UPDATE pipeline_job SET status='queued',started_at=NULL,
                          completed_at=NULL,error_text=? WHERE pipeline_job_id=?""",
                (error[:2000], job["pipeline_job_id"]),
            )
        db.commit()


def call_space(space: str, token: str, jobs: list[dict], timeout: float) -> dict:
    host = space.replace("/", "-").lower() + ".hf.space"
    base = f"https://{host}/gradio_api/call/extract_batch"
    payload = json.dumps([
        {"id": job["evidence_span_id"], "text": job["evidence_text"]}
        for job in jobs
    ], ensure_ascii=False)
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        submitted = client.post(base, headers=headers, json={"data": [payload]})
        submitted.raise_for_status()
        event_id = submitted.json()["event_id"]
        response = client.get(f"{base}/{event_id}", headers=headers)
        response.raise_for_status()
    completed = None
    failure = None
    event = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            if event == "error":
                failure = json.dumps(data, ensure_ascii=False)[:1800]
            elif isinstance(data, list) and data and isinstance(data[0], str):
                completed = json.loads(data[0])
            elif isinstance(data, dict) and data.get("error"):
                failure = str(data["error"])
    if completed is None:
        raise RuntimeError(failure or "ZeroGPU Space returned no completed result")
    return completed


def persist_result(path: Path, job: dict, model: str, item: dict) -> dict:
    if item.get("error"):
        raise ValueError(item["error"])
    candidate = RelationExtraction.model_validate(item.get("candidate"))
    evidence_id = job["evidence_span_id"]
    input_sha = sha256_text(job["evidence_text"])
    prompt_sha = sha256_text(SYSTEM_PROMPT + "\n" + SCHEMA_VERSION + "\nzerogpu-v1")
    extraction_id = stable_id(
        "extraction-job", "huggingface-zerogpu", model, prompt_sha, input_sha
    )
    now = datetime.now(UTC).isoformat()
    db_module.DB_PATH = path
    with db_module.transaction() as db:
        db.execute(
            """INSERT INTO extraction_job
               (extraction_job_id,provider,model,prompt_sha256,input_sha256,
                response_sha256,source_url,raw_response_json,token_cost_json,
                status,review_status,created_at,completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,'needs_review','needs_review',?,?)
               ON CONFLICT(extraction_job_id) DO UPDATE SET
                 response_sha256=excluded.response_sha256,
                 raw_response_json=excluded.raw_response_json,
                 status='needs_review',review_status='needs_review',
                 completed_at=excluded.completed_at""",
            (
                extraction_id,"huggingface-zerogpu",model,prompt_sha,input_sha,
                sha256_text(json.dumps(item["candidate"],sort_keys=True)),
                job["source_url"],json.dumps({"candidate":item["candidate"]},ensure_ascii=False),
                json.dumps({"gpu_provider":"zerogpu","billed_api_tokens":0}),now,now,
            ),
        )
    validation = persist_candidate(evidence_id, extraction_id, candidate)
    result = {
        "extraction_job_id": extraction_id,
        "provider": "huggingface-zerogpu",
        "model": model,
        "review_status": "needs_review",
        "validation_counts": validation,
    }
    with connect(path) as db:
        original = json.loads(job["result_json"])
        original["result"] = result
        db.execute(
            """UPDATE pipeline_job SET status='succeeded',completed_at=?,
                      result_json=?,error_text=NULL WHERE pipeline_job_id=?""",
            (datetime.now(UTC).isoformat(),json.dumps(original),job["pipeline_job_id"]),
        )
        db.commit()
    return result


def mark_failed(path: Path, job: dict, error: str) -> None:
    with connect(path) as db:
        db.execute(
            """UPDATE pipeline_job SET status='failed',completed_at=?,error_text=?
               WHERE pipeline_job_id=?""",
            (datetime.now(UTC).isoformat(),error[:2000],job["pipeline_job_id"]),
        )
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file",type=Path,default=ROOT/".env")
    parser.add_argument("--db",type=Path,default=DEFAULT_DB)
    parser.add_argument("--space",default=DEFAULT_SPACE)
    parser.add_argument("--batch-size",type=int,default=4,choices=range(1,9))
    parser.add_argument("--max-batches",type=int,default=0,help="0 runs until queue completion")
    parser.add_argument("--idle-seconds",type=int,default=300)
    parser.add_argument("--timeout",type=float,default=600)
    args=parser.parse_args()
    load_env(args.env_file)
    token=os.getenv("HF_SPACE_TOKEN") or os.getenv("HF_TOKEN1")
    if not token:
        raise SystemExit("Configure HF_SPACE_TOKEN or HF_TOKEN1")
    completed_batches=0; delay=max(60,args.idle_seconds)
    while args.max_batches==0 or completed_batches<args.max_batches:
        jobs=claim_batch(args.db,args.batch_size)
        if not jobs:
            print(f"{datetime.now(UTC).isoformat()} no eligible queued jobs: {queue_counts(args.db)}")
            return
        try:
            response=call_space(args.space,token,jobs,args.timeout)
            by_id={item["id"]:item for item in response.get("results",[])}
            for job in jobs:
                item=by_id.get(job["evidence_span_id"])
                if not item:
                    mark_failed(args.db,job,"missing_result_from_zerogpu")
                    continue
                try:
                    persist_result(args.db,job,response.get("model",DEFAULT_MODEL),item)
                except Exception as error:
                    mark_failed(args.db,job,f"ZeroGPU candidate validation failed: {error}")
            completed_batches += 1; delay=max(60,args.idle_seconds)
            print(f"{datetime.now(UTC).isoformat()} batch={completed_batches} queue={queue_counts(args.db)}",flush=True)
        except Exception as error:
            requeue(args.db,jobs,f"ZeroGPU delayed retry: {error}")
            print(f"{datetime.now(UTC).isoformat()} retry in {delay}s: {error}",flush=True)
            if args.max_batches:
                raise
            time.sleep(delay); delay=min(delay*2,3600)


if __name__ == "__main__":
    main()