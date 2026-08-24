#!/usr/bin/env python3
"""Show live RXN2 relation extraction counts by exclusive execution lane."""
from __future__ import annotations
import argparse,sqlite3,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def show(db):
 with sqlite3.connect(db) as c:
  rows=c.execute("""SELECT coalesce(json_extract(result_json,'$.execution_lane'),'unassigned'),status,count(*) FROM pipeline_job WHERE job_type='relation_extraction' GROUP BY 1,2 ORDER BY 1,2""").fetchall()
  print(' | '.join(f'{lane}: {status}={n}' for lane,status,n in rows))
def main():
 p=argparse.ArgumentParser();p.add_argument('--db',type=Path,default=ROOT/'data/curated/rxn2-provisional.sqlite');p.add_argument('--watch',action='store_true');p.add_argument('--seconds',type=int,default=20);a=p.parse_args()
 while True:
  show(a.db)
  if not a.watch:return
  time.sleep(max(2,a.seconds))
if __name__=='__main__':main()
