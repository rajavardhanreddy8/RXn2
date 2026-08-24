#!/usr/bin/env python3
"""Single-T4, resumable RXN2 relation extraction runner for Google Colab."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

DRIVE_ROOT = Path("/content/drive/MyDrive/RXN2/relation-extraction/overnight-v2")
LOCAL_ROOT = Path("/content/rxn2-relation-overnight")
CHECKPOINT_RECORDS = 10
CHECKPOINT_SECONDS = 120
MAX_ATTEMPTS = 2


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(partial, path)


def drive_status(state: str, **values) -> None:
    atomic_json(DRIVE_ROOT / "status.json", {"state": state, "updated_at": now(), **values})


def verify_bundle() -> dict:
    manifest_path = DRIVE_ROOT / "jobs" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        path = DRIVE_ROOT / relative
        actual = sha256_file(path)
        if actual != expected:
            raise RuntimeError(f"checksum_mismatch:{relative}:{actual}:{expected}")
    return manifest


def load_runtime():
    runner_dir = DRIVE_ROOT / "runner"
    if str(runner_dir) not in sys.path:
        sys.path.insert(0, str(runner_dir))
    from colab_relation_common import (  # noqa: PLC0415
        MODEL_NAME, RELATION_SCHEMA, job_hash, pack_jobs, validate_candidate,
    )
    return MODEL_NAME, RELATION_SCHEMA, job_hash, pack_jobs, validate_candidate


def copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    shutil.copyfile(source, partial)
    os.replace(partial, destination)


def part_number(path: Path) -> int:
    match = re.search(r"part-(\d+)\.json$", path.name)
    return int(match.group(1)) if match else 0


def load_outcomes() -> tuple[set[str], int]:
    outcomes: set[str] = set()
    highest = 0
    for manifest_path in sorted((DRIVE_ROOT / "manifest").glob("part-*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        highest = max(highest, part_number(manifest_path))
        for kind in ("results", "retry"):
            details = manifest.get(kind)
            if not details:
                continue
            artifact = DRIVE_ROOT / details["path"]
            if sha256_file(artifact) != details["sha256"]:
                raise RuntimeError(f"checkpoint_checksum_mismatch:{artifact}")
            for line in artifact.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    if kind == "results" or record.get("terminal"):
                        outcomes.add(record["input_sha256"])
    return outcomes, highest + 1


class Checkpoints:
    def __init__(self, part: int, model_name: str, prompt_sha256: str):
        self.part = part
        self.model_name = model_name
        self.prompt_sha256 = prompt_sha256
        self.results: list[dict] = []
        self.retry: list[dict] = []
        self.last_flush = time.monotonic()

    def due(self) -> bool:
        return len(self.results) + len(self.retry) >= CHECKPOINT_RECORDS or time.monotonic() - self.last_flush >= CHECKPOINT_SECONDS

    def flush(self) -> None:
        if not self.results and not self.retry:
            return
        tag = f"part-{self.part:05d}"
        local_dir = LOCAL_ROOT / "parts"
        local_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "part": self.part, "created_at": now(), "model": self.model_name,
            "prompt_sha256": self.prompt_sha256,
            "success_count": len(self.results), "terminal_failure_count": len(self.retry),
        }
        for kind, records in (("results", self.results), ("retry", self.retry)):
            if not records:
                manifest[kind] = None
                continue
            local = local_dir / f"{tag}-{kind}.jsonl"
            local.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
            relative = f"{kind}/{tag}.jsonl"
            copy_atomic(local, DRIVE_ROOT / relative)
            manifest[kind] = {"path": relative, "sha256": sha256_file(local), "records": len(records)}
        local_manifest = local_dir / f"{tag}.json"
        atomic_json(local_manifest, manifest)
        copy_atomic(local_manifest, DRIVE_ROOT / "manifest" / f"{tag}.json")
        self.part += 1
        self.results.clear(); self.retry.clear(); self.last_flush = time.monotonic()


def json_object(raw: str) -> dict:
    value = json.loads(raw.strip())
    if not isinstance(value, dict):
        raise ValueError("model_output_not_object")
    return value


def main() -> None:
    drive_status("starting")
    manifest = verify_bundle()
    model_name, schema, job_hash, pack_jobs, validate_candidate = load_runtime()
    jobs_path = DRIVE_ROOT / "jobs" / "jobs.jsonl"
    jobs = [json.loads(line) for line in jobs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    outcomes, next_part = load_outcomes()
    pending = [job for job in jobs if job_hash(job) not in outcomes]
    started = datetime.now(UTC)
    drive_status("loading_model", total=len(jobs), completed=len(outcomes), remaining=len(pending))

    import torch  # noqa: PLC0415
    from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415
    from lmformatenforcer import JsonSchemaParser  # noqa: PLC0415
    from lmformatenforcer.integrations.transformers import build_transformers_prefix_allowed_tokens_fn  # noqa: PLC0415

    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    model = AutoModelForCausalLM.from_pretrained(
        model_name, torch_dtype=torch.float16, device_map="auto",
        trust_remote_code=True, low_cpu_mem_usage=True,
    ).eval()
    parser = JsonSchemaParser(schema)
    prefix_allowed = build_transformers_prefix_allowed_tokens_fn(tokenizer, parser)
    prompt = (DRIVE_ROOT / "jobs" / "relation-prompt.txt").read_text(encoding="utf-8")
    checkpoints = Checkpoints(next_part, model_name, manifest["prompt_sha256"])
    session_success = session_failed = processed = 0

    def update_status(state: str, current: list[dict] | None = None) -> None:
        elapsed = max((datetime.now(UTC) - started).total_seconds(), 0.001)
        rate = processed / elapsed * 60
        remaining = len(pending) - processed
        eta = (datetime.now(UTC) + timedelta(minutes=remaining / rate)).isoformat() if rate > 0 else None
        drive_status(
            state, total=len(jobs), previously_completed=len(outcomes),
            session_success=session_success, terminal_failures=session_failed,
            completed=len(outcomes) + processed, remaining=max(0, remaining),
            procedures_per_minute=round(rate, 3), estimated_finish_at=eta,
            current_evidence_span_ids=[job["evidence_span_id"] for job in current or []],
        )

    def infer(items: list[dict]) -> list[tuple[dict | None, str | None]]:
        conversations = [[{"role": "system", "content": prompt}, {"role": "user", "content": item["evidence_text"]}] for item in items]
        prompts = [tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False) for messages in conversations]
        inputs = tokenizer(prompts, padding=True, truncation=True, max_length=4096, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            generated = model.generate(
                **inputs, max_new_tokens=1600, do_sample=False,
                prefix_allowed_tokens_fn=prefix_allowed,
                pad_token_id=tokenizer.pad_token_id,
            )
        width = inputs["input_ids"].shape[-1]
        raw_outputs = tokenizer.batch_decode(generated[:, width:], skip_special_tokens=True)
        results = []
        for item, raw in zip(items, raw_outputs, strict=True):
            try:
                candidate = json_object(raw)
                validate_candidate(candidate, item["evidence_text"])
                results.append((candidate, None))
            except Exception as error:
                results.append((None, f"{type(error).__name__}: {error}"))
        return results

    def singleton(item: dict) -> tuple[dict | None, str | None]:
        error_text = None
        for _ in range(MAX_ATTEMPTS):
            try:
                candidate, error_text = infer([item])[0]
                if candidate is not None:
                    return candidate, None
            except torch.cuda.OutOfMemoryError as error:
                error_text = f"CUDAOutOfMemoryError: {error}"
                torch.cuda.empty_cache()
            except Exception as error:
                error_text = f"{type(error).__name__}: {error}"
        return None, error_text or "unknown_inference_failure"

    batches = pack_jobs(pending, max_items=4, max_chars=16_000)
    smoke_remaining = min(10, len(pending))
    for batch_number, batch in enumerate(batches, 1):
        update_status("smoke_test" if smoke_remaining else "running", batch)
        try:
            batch_results = infer(batch)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            batch_results = [(None, "batch_cuda_out_of_memory") for _ in batch]
        except Exception as error:
            batch_results = [(None, f"batch_{type(error).__name__}: {error}") for _ in batch]
        for item, (candidate, error_text) in zip(batch, batch_results, strict=True):
            if candidate is None:
                candidate, error_text = singleton(item)
            base = {
                "input_sha256": job_hash(item), "pipeline_job_id": item["pipeline_job_id"],
                "evidence_span_id": item["evidence_span_id"], "publication_number": item["publication_number"],
                "model": model_name, "schema_version": manifest["schema_version"],
                "prompt_sha256": manifest["prompt_sha256"], "created_at": now(),
            }
            if candidate is not None:
                checkpoints.results.append({**base, "candidate": candidate, "review_status": "needs_review"})
                session_success += 1
            else:
                checkpoints.retry.append({**base, "terminal": True, "error": error_text})
                session_failed += 1
            processed += 1
            smoke_remaining = max(0, smoke_remaining - 1)
            if checkpoints.due():
                update_status("checkpointing", batch)
                checkpoints.flush()
            update_status("smoke_test" if smoke_remaining else "running", batch)
            print(f"{processed}/{len(pending)} this session | success={session_success} failed={session_failed} | {item['evidence_span_id']}", flush=True)
    checkpoints.flush()
    update_status("completed")


if __name__ == "__main__":
    main()
