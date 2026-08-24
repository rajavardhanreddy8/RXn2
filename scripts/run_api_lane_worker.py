#!/usr/bin/env python3
"""Run one rate-limit-aware worker against only the RXN2 API execution lane."""
from __future__ import annotations
import argparse, asyncio, json, os, sqlite3, sys, time
from datetime import UTC, datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from apps.api.app import db as db_module
from apps.api.app.relations import process_evidence_span
TRANSIENT = ("429", "rate limit", "timeout", "timed out", "503", "temporarily", "model is busy", "completion_error", "unprocessable entity")

def load_env(path: Path):
    if not path.is_file(): return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k,v=line.split("=",1); os.environ.setdefault(k.strip(),v.strip())

def counts(db_path: Path):
    with sqlite3.connect(db_path) as db:
        return dict(db.execute("""SELECT status,count(*) FROM pipeline_job
          WHERE job_type='relation_extraction'
            AND coalesce(json_extract(result_json,'$.execution_lane'),'overnight')='api'
          GROUP BY status"""))

def requeue_transient(db_path: Path):
    with sqlite3.connect(db_path) as db:
        rows=db.execute("""SELECT pipeline_job_id,error_text FROM pipeline_job
          WHERE job_type='relation_extraction' AND status='failed'
            AND coalesce(json_extract(result_json,'$.execution_lane'),'overnight')='api'""").fetchall()
        ids=[job_id for job_id,error in rows if any(x in (error or '').casefold() for x in TRANSIENT)]
        for job_id in ids:
            db.execute("UPDATE pipeline_job SET status='queued',started_at=NULL,completed_at=NULL,error_text='transient provider failure; delayed retry' WHERE pipeline_job_id=?", (job_id,))
        db.commit()
    return len(ids)

def claim(db_path: Path):
    with sqlite3.connect(db_path, timeout=90) as db:
        db.row_factory=sqlite3.Row; db.execute("PRAGMA busy_timeout=90000"); db.execute("BEGIN IMMEDIATE")
        row=db.execute("""SELECT pipeline_job_id,result_json FROM pipeline_job
          WHERE job_type='relation_extraction' AND status='queued'
            AND coalesce(json_extract(result_json,'$.execution_lane'),'overnight')='api'
          ORDER BY attempt_count,queued_at LIMIT 1""").fetchone()
        if not row: db.commit(); return None
        db.execute("UPDATE pipeline_job SET status='running',started_at=?,attempt_count=attempt_count+1 WHERE pipeline_job_id=? AND status='queued'", (datetime.now(UTC).isoformat(),row['pipeline_job_id']))
        db.commit(); return dict(row)

def finish(db_path: Path, job_id: str, payload: dict, status: str, error: str|None):
    with sqlite3.connect(db_path,timeout=90) as db:
        db.execute("PRAGMA busy_timeout=90000")
        db.execute("UPDATE pipeline_job SET status=?,completed_at=?,result_json=?,error_text=? WHERE pipeline_job_id=?", (status,datetime.now(UTC).isoformat(),json.dumps(payload),error,job_id)); db.commit()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--db',type=Path,default=ROOT/'data/curated/rxn2-provisional.sqlite'); parser.add_argument('--env-file',type=Path,default=ROOT/'.env'); parser.add_argument('--provider',choices=('openrouter','groq'),required=True); parser.add_argument('--idle-seconds',type=int,default=30); parser.add_argument('--max-jobs',type=int,default=0)
    args=parser.parse_args(); load_env(args.env_file); db_module.DB_PATH=args.db
    if not (os.getenv('OPENROUTER_API_KEY') if args.provider=='openrouter' else os.getenv('GROQ_API_KEY')): raise SystemExit('Provider key is not configured.')
    completed=0; delay=max(10,args.idle_seconds)
    while args.max_jobs==0 or completed<args.max_jobs:
        job=claim(args.db)
        if not job:
            retried=requeue_transient(args.db)
            state=counts(args.db)
            if not retried and not state.get('queued',0) and not state.get('running',0):
                print(json.dumps({'state':'api_lane_complete','counts':state})); return
            print(f"{datetime.now(UTC).isoformat()} api lane waiting {delay}s; {state}")
            time.sleep(delay); delay=min(delay*2,3600); continue
        payload=json.loads(job['result_json']); payload['provider_mode']=args.provider
        try:
            result=asyncio.run(process_evidence_span(payload['evidence_span_id'],provider=args.provider))
            payload['result']=result; finish(args.db,job['pipeline_job_id'],payload,'succeeded',None); completed+=1; delay=max(10,args.idle_seconds)
            print(f"{datetime.now(UTC).isoformat()} completed={completed} {counts(args.db)}")
        except Exception as exc:
            finish(args.db,job['pipeline_job_id'],payload,'failed',str(exc)); print(f"{datetime.now(UTC).isoformat()} failure: {str(exc)[:200]}")
if __name__=='__main__': main()
