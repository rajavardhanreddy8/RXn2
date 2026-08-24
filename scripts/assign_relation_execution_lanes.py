#!/usr/bin/env python3
"""Assign every queued relation-extraction job to one exclusive execution lane."""
from __future__ import annotations
import argparse, json, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data/curated/rxn2-provisional.sqlite")
    parser.add_argument("--api-count", type=int, default=200)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect(args.db) as db:
        db.row_factory = sqlite3.Row
        running = db.execute("SELECT count(*) FROM pipeline_job WHERE job_type='relation_extraction' AND status='running'").fetchone()[0]
        if running:
            raise SystemExit(f"Refusing lane reassignment while {running} jobs are running.")
        jobs = list(db.execute("""
            SELECT p.pipeline_job_id,p.result_json,e.evidence_text
            FROM pipeline_job p JOIN evidence_span e
              ON e.evidence_span_id=json_extract(p.result_json,'$.evidence_span_id')
            WHERE p.job_type='relation_extraction' AND p.status='queued'
            ORDER BY CASE json_extract(p.result_json,'$.candidate_status')
              WHEN 'participant_roles_partial' THEN 0 ELSE 1 END,
              p.attempt_count,length(e.evidence_text),p.queued_at
        """))
        api_jobs = jobs[:max(0, args.api_count)]
        report = {"queued": len(jobs), "api": len(api_jobs), "overnight": len(jobs)-len(api_jobs), "applied": args.apply}
        if args.apply:
            db.execute("BEGIN IMMEDIATE")
            for index, row in enumerate(jobs):
                payload = json.loads(row["result_json"] or "{}")
                payload["execution_lane"] = "api" if index < len(api_jobs) else "overnight"
                db.execute("UPDATE pipeline_job SET result_json=? WHERE pipeline_job_id=?", (json.dumps(payload, separators=(",", ":")), row["pipeline_job_id"]))
            db.commit()
    print(json.dumps(report, indent=2, sort_keys=True))

if __name__ == "__main__": main()
