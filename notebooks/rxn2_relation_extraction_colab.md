# RXN2 T4 relation extraction

This notebook workflow runs Qwen3-4B on a Colab T4. It reads only compact JSONL
jobs from Drive and writes resumable result/retry JSONL files. It never opens the
RXN2 SQLite database from Drive.

## Cell 1: install

```python
!pip -q install "transformers==4.57.6" "accelerate>=1.10,<2" "huggingface_hub>=0.34,<1" "lm-format-enforcer>=0.11,<1"
```

## Cell 2: GPU and Drive

```python
from google.colab import drive
drive.mount("/content/drive")
import torch
assert torch.cuda.is_available(), "Runtime -> Change runtime type -> T4 GPU"
print(torch.cuda.get_device_name(0))
```

## Cell 3: model

```python
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

MODEL_NAME = "Qwen/Qwen3-4B-Instruct-2507"
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME, torch_dtype=torch.float16, device_map="auto",
    trust_remote_code=True, low_cpu_mem_usage=True,
).eval()
print("loaded", MODEL_NAME, next(model.parameters()).device)
```

## Cell 4: paths and deterministic extraction

```python
from pathlib import Path
import hashlib, json, torch

ROOT = Path("/content/drive/MyDrive/RXN2/relation-extraction")
INPUT = ROOT / "jobs" / "jobs.jsonl"
OUTPUT = ROOT / "results" / "results.jsonl"
RETRY = ROOT / "retry" / "retry.jsonl"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
RETRY.parent.mkdir(parents=True, exist_ok=True)

SYSTEM = """Extract factual relations from one performed patent procedure.
Return JSON only. Copy names and supporting_quote verbatim from the supplied text.
Never generate SMILES, InChIKeys, reaction SMILES, identities, quantities, yields,
or conditions that are not explicit. If this is not a performed procedure set
procedure_performed=false. Never approve chemistry.
Schema: {procedure_performed:boolean, materials:[{surface_text:string,role:string,
explicit:boolean}], conditions:object, outcome:object, supporting_quote:string,
uncertainties:[string]}"""

def input_hash(job):
    return hashlib.sha256((job["evidence_span_id"] + "\n" + job["evidence_text"]).encode()).hexdigest()

def extract(text):
    prompt = SYSTEM + "\n\nPROCEDURE:\n" + text
    tokens = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=12000).to(model.device)
    with torch.inference_mode():
        generated = model.generate(**tokens, max_new_tokens=768, do_sample=False,
                                   pad_token_id=tokenizer.eos_token_id)
    raw = tokenizer.decode(generated[0][tokens["input_ids"].shape[1]:], skip_special_tokens=True)
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("model did not return JSON")
    return json.loads(raw[start:end])
```

## Cell 5: resumable runner

```python
completed = set()
if OUTPUT.exists():
    for line in OUTPUT.read_text(encoding="utf-8").splitlines():
        if line.strip(): completed.add(json.loads(line)["input_sha256"])

with INPUT.open(encoding="utf-8") as source, OUTPUT.open("a", encoding="utf-8") as out, RETRY.open("a", encoding="utf-8") as retry:
    for line in source:
        if not line.strip(): continue
        job = json.loads(line)
        digest = input_hash(job)
        if digest in completed: continue
        try:
            record = {"input_sha256": digest, "evidence_span_id": job["evidence_span_id"],
                      "publication_number": job.get("publication_number"),
                      "candidate": extract(job["evidence_text"]),
                      "model": MODEL_NAME}
            out.write(json.dumps(record, ensure_ascii=False) + "\n"); out.flush()
            completed.add(digest)
        except Exception as exc:
            retry.write(json.dumps({"input_sha256": digest, "evidence_span_id": job["evidence_span_id"], "error": str(exc)}) + "\n")
            retry.flush()
print("completed", len(completed))
```

Run Cell 5 again after a disconnect. It skips completed hashes and continues.

## Local preparation

```powershell
python scripts\export_relation_jobs.py `
  --db data\curated\rxn2-provisional.sqlite `
  --output "I:\My Drive\RXN2\relation-extraction\jobs\jobs.jsonl"
```

After Colab finishes, download/copy `results.jsonl` and `retry.jsonl` back for
local validation/import. The importer must verify exact quotations, offsets,
known compound mappings and performed-procedure status before writing the graph.
No result may become `accepted` automatically.
