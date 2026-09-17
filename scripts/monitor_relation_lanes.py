#!/usr/bin/env python3
"""Show live RXN2 relation extraction counts by exclusive execution lane."""
from __future__ import annotations
import argparse,sqlite3,time
from datetime import UTC, datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def show(db):
 with sqlite3.connect(db) as c:
  rows=c.execute("""SELECT coalesce(json_extract(result_json,'$.execution_lane'),'unassigned'),status,count(*) FROM pipeline_job WHERE job_type='relation_extraction' GROUP BY 1,2 ORDER BY 1,2""").fetchall()
  totals={}
  for lane,status,n in rows:
   totals[status]=totals.get(status,0)+n
  if totals.get('running',0): state='LIVE'
  elif totals.get('queued',0): state='STOPPED — queued work has no active worker'
  else: state='COMPLETE — no queued or active work'
  stamp=datetime.now(UTC).strftime('%H:%M:%S UTC')
  print(f'[{stamp}] {state} | ' + ' | '.join(f'{lane}: {status}={n}' for lane,status,n in rows))
def main():
 p=argparse.ArgumentParser();p.add_argument('--db',type=Path,default=ROOT/'data/curated/rxn2-provisional.sqlite');p.add_argument('--watch',action='store_true');p.add_argument('--seconds',type=int,default=20);a=p.parse_args()
 while True:
  show(a.db)
  if not a.watch:return
  time.sleep(max(2,a.seconds))
if __name__=='__main__':main()
