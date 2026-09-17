"""Export an idempotent, evidence-preserving retry queue for incomplete Phase 1 reviews.

This does not call a model and does not alter either RXN2 database.  It makes
every non-exported reviewed example actionable with a precise, recorded reason.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / "data/processed/review/process-patent-groq-qwen-full-20260831"
DEFAULT_PHASE1 = ROOT / "data/processed/evaluation/phase1-source-evaluation-family-safe.jsonl"
DEFAULT_OUTPUT = ROOT / "data/processed/review/phase2-recovery-queue.jsonl"
DEFAULT_MANIFEST = ROOT / "data/processed/review/phase2-recovery-queue.manifest.json"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(content, encoding="utf-8")
    partial.replace(path)


def classify(proposal: dict) -> str:
    reviews = proposal.get("reviews", {})
    failures = proposal.get("local_validation_failures", {})
    if not reviews:
        return "missing_both_reviews"
    if len(reviews) == 1:
        missing = {"source_completeness", "adversarial_chemistry"} - set(reviews)
        return "missing_" + next(iter(missing))
    if any(failures.get(role) for role in ("source_completeness", "adversarial_chemistry")):
        return "quote_or_schema_repair"
    if proposal.get("consensus", {}).get("status") == "agent_disagreement":
        return "human_adjudication_required"
    return "manual_triage_required"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--phase1", type=Path, default=DEFAULT_PHASE1)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    sources: dict[str, dict] = {}
    for path in sorted(glob.glob(str(args.run / "worker-*/input.jsonl"))):
        for item in read_jsonl(Path(path)):
            if item["example_id"] in sources:
                raise ValueError(f"duplicate input example: {item['example_id']}")
            sources[item["example_id"]] = item
    proposals: dict[str, dict] = {}
    for path in sorted(glob.glob(str(args.run / "worker-*/results/agent_review_proposals.jsonl"))):
        for item in read_jsonl(Path(path)):
            if item["example_id"] in proposals:
                raise ValueError(f"duplicate proposal example: {item['example_id']}")
            proposals[item["example_id"]] = item
    exported = {item["example_id"] for item in read_jsonl(args.phase1)}
    if set(proposals) != set(sources):
        raise ValueError("review input/proposal mismatch")
    if not exported <= set(proposals):
        raise ValueError("Phase 1 dataset contains unknown example")

    queue = []
    reasons = Counter()
    for example_id, proposal in sorted(proposals.items()):
        if example_id in exported:
            continue
        reason = classify(proposal)
        reasons[reason] += 1
        source = sources[example_id]
        queue.append({
            "schema_version": "rxn2-phase2-recovery-queue-v1",
            "example_id": example_id,
            "priority": 1 if reason in {"missing_both_reviews", "missing_source_completeness", "missing_adversarial_chemistry"} else 2,
            "recovery_reason": reason,
            "publication_number": proposal["publication_number"],
            "heading": proposal["heading"],
            "text": source["text"],
            "text_sha256": proposal["text_sha256"],
            "source_artifact_sha256": proposal["source_artifact_sha256"],
            "existing_review_roles": sorted(proposal.get("reviews", {})),
            "local_validation_failures": proposal.get("local_validation_failures", {}),
            "automatic_acceptance": False,
            "requires_human_review": True,
        })
    content = "".join(json.dumps(item, sort_keys=True) + "\n" for item in queue)
    write_atomic(args.output, content)
    manifest = {
        "schema_version": "rxn2-phase2-recovery-queue-manifest-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "reviewed_examples": len(proposals), "phase1_exported": len(exported),
        "recovery_records": len(queue), "reasons": dict(sorted(reasons.items())),
        "input_sha256": hashlib.sha256("".join(sorted(item["text_sha256"] for item in proposals.values())).encode()).hexdigest(),
        "queue_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "automatic_acceptance": False, "database_mutated": False,
    }
    write_atomic(args.manifest, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
