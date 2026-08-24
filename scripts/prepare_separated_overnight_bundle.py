#!/usr/bin/env python3
"""Build the immutable Colab bundle from the overnight lane only."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, sqlite3, sys
from collections import Counter
from datetime import UTC,datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.colab_relation_common import MODEL_NAME,PROMPT_SHA256,RELATION_SCHEMA,SCHEMA_VERSION,SYSTEM_PROMPT,job_hash
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def atomic(p,text):
 p.parent.mkdir(parents=True,exist_ok=True); x=p.with_suffix(p.suffix+'.partial'); x.write_text(text,encoding='utf-8'); os.replace(x,p)
def main():
 a=argparse.ArgumentParser();a.add_argument('--db',type=Path,default=ROOT/'data/curated/rxn2-provisional.sqlite');a.add_argument('--run-root',type=Path,default=Path(r'I:\My Drive\RXN2\relation-extraction\overnight-v2'));a.add_argument('--maximum-chars',type=int,default=12000);args=a.parse_args()
 query="""SELECT p.pipeline_job_id,p.result_json,e.evidence_span_id,e.publication_number,e.evidence_text,e.source_url FROM pipeline_job p JOIN evidence_span e ON e.evidence_span_id=json_extract(p.result_json,'$.evidence_span_id') WHERE p.job_type='relation_extraction' AND p.status='queued' AND coalesce(json_extract(p.result_json,'$.execution_lane'),'overnight')='overnight' ORDER BY CASE json_extract(p.result_json,'$.candidate_status') WHEN 'participant_roles_partial' THEN 0 ELSE 1 END,p.attempt_count,length(e.evidence_text),p.queued_at"""
 jobs=[]
 with sqlite3.connect(args.db) as db:
  db.row_factory=sqlite3.Row
  for r in db.execute(query):
   if len(r['evidence_text'])>args.maximum_chars:raise RuntimeError(f"Oversized queued evidence: {r['evidence_span_id']}")
   x=json.loads(r['result_json'] or '{}'); job={k:r[k] for k in ('pipeline_job_id','evidence_span_id','publication_number','evidence_text','source_url')};job.update(candidate_status=x.get('candidate_status','evidence_only'),parent_evidence_span_id=x.get('parent_evidence_span_id'),chunk_index=x.get('chunk_index'),chunk_count=x.get('chunk_count'));job['input_sha256']=job_hash(job);jobs.append(job)
 root=args.run_root; inp=root/'jobs/jobs.jsonl';atomic(inp,''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in jobs));atomic(root/'jobs/relation-schema.json',json.dumps(RELATION_SCHEMA,ensure_ascii=False,indent=2,sort_keys=True));atomic(root/'jobs/relation-prompt.txt',SYSTEM_PROMPT)
 paths={}
 for name in ('colab_relation_common.py','colab_overnight_runner.py'):
  dest=root/'runner'/name; dest.parent.mkdir(parents=True,exist_ok=True);partial=dest.with_suffix('.py.partial');shutil.copyfile(ROOT/'scripts'/name,partial);os.replace(partial,dest);paths[name]=dest
 manifest={'created_at':datetime.now(UTC).isoformat(),'schema_version':SCHEMA_VERSION,'model':MODEL_NAME,'prompt_sha256':PROMPT_SHA256,'records':len(jobs),'candidate_status_counts':dict(sorted(Counter(j['candidate_status'] for j in jobs).items())),'maximum_evidence_chars':max((len(j['evidence_text']) for j in jobs),default=0),'execution_lane':'overnight','files':{'jobs/jobs.jsonl':sha(inp),'jobs/relation-schema.json':sha(root/'jobs/relation-schema.json'),'jobs/relation-prompt.txt':sha(root/'jobs/relation-prompt.txt'),**{'runner/'+n:sha(p) for n,p in paths.items()}},'legacy_results':'../results/results.jsonl','legacy_results_policy':'preserved_unvalidated_not_counted'}
 atomic(root/'jobs/manifest.json',json.dumps(manifest,indent=2,sort_keys=True));print(json.dumps(manifest,indent=2,sort_keys=True))
if __name__=='__main__':main()
