#!/usr/bin/env python3
"""Launch one API-lane worker per separately authorized OpenRouter or Groq project key."""
from __future__ import annotations
import argparse, os, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OR=("op_api_key",)+tuple(f"op_api_key{i}" for i in range(32))
GROQ=("q_api_key",)+tuple(f"q_api_key{i}" for i in range(32))
def load(path):
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            k,v=line.split('=',1); os.environ.setdefault(k.strip(),v.strip())
def keys(names):
    seen=set(); out=[]
    for name in names:
        value=os.getenv(name)
        if value and value not in seen: seen.add(value); out.append((name,value))
    return out
def main():
    p=argparse.ArgumentParser(); p.add_argument('--env-file',type=Path,default=ROOT/'.env'); p.add_argument('--idle-seconds',type=int,default=60); args=p.parse_args(); load(args.env_file)
    configured=[('openrouter',n,k) for n,k in keys(OR)]+[('groq',n,k) for n,k in keys(GROQ)]
    if not configured: raise SystemExit('No authorized project keys configured.')
    logs=ROOT/'data/processed'; logs.mkdir(parents=True,exist_ok=True); children=[]
    for i,(provider,name,key) in enumerate(configured,1):
        env=os.environ.copy()
        for n in OR+GROQ: env.pop(n,None)
        env['RELATION_QUEUE_LANE']='api'; env['SCALEUP_DB_PATH']=str(ROOT/'data/curated/rxn2-provisional.sqlite')
        if provider=='openrouter':
            env['OPENROUTER_API_KEY']=key; env['RELATION_OPENROUTER_MODELS']='z-ai/glm-5.2:free,nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free'
        else:
            env['GROQ_API_KEY']=key; env['RELATION_GROQ_MODEL']='openai/gpt-oss-20b'
        out=(logs/f'api-lane-worker-{i}.log').open('a',encoding='utf-8'); err=(logs/f'api-lane-worker-{i}.err.log').open('a',encoding='utf-8')
        child=subprocess.Popen([sys.executable,'scripts/run_api_lane_worker.py','--provider',provider,'--idle-seconds',str(args.idle_seconds)],cwd=ROOT,env=env,stdout=out,stderr=err); children.append(child); print(f'started {provider} worker {i} for approved project {name}; pid={child.pid}')
    for child in children: child.wait()
if __name__=='__main__': main()
