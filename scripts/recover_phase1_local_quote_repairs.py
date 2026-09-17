"""Revalidate legacy Phase 1 reviews after deterministic quote re-anchoring.

No model is called and the original review records are untouched.  Results are
an auditable overlay; only exact unique source-text anchors may be repaired.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from scripts.review_patent_examples_with_llm import ReviewProposal, comparison, repair_quotes, validate_quotes


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / "data/processed/review/process-patent-groq-qwen-full-20260831"
DEFAULT_OUTPUT = ROOT / "data/processed/review/phase2-local-quote-repairs.jsonl"
DEFAULT_MANIFEST = ROOT / "data/processed/review/phase2-local-quote-repairs.manifest.json"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    source_by_id = {}
    for path in sorted(glob.glob(str(args.run / "worker-*/input.jsonl"))):
        source_by_id.update({item["example_id"]: item for item in read_jsonl(Path(path))})
    repaired_records, statuses = [], Counter()
    for path in sorted(glob.glob(str(args.run / "worker-*/results/agent_review_proposals.jsonl"))):
        for legacy in read_jsonl(Path(path)):
            roles = legacy.get("reviews", {})
            if len(roles) != 2:
                continue
            source = source_by_id[legacy["example_id"]]["text"]
            output_roles, changes, failures = {}, {}, {}
            for role, payload in roles.items():
                proposal, updates = repair_quotes(source, ReviewProposal.model_validate(payload))
                output_roles[role] = proposal.model_dump()
                changes[role] = updates
                failures[role] = validate_quotes(source, proposal)
            if any(failures.values()):
                status = "still_invalid"
                consensus = None
            else:
                consensus = comparison(
                    ReviewProposal.model_validate(output_roles["source_completeness"]),
                    ReviewProposal.model_validate(output_roles["adversarial_chemistry"]),
                )
                status = consensus["status"]
            statuses[status] += 1
            if any(changes.values()) or status == "agent_agreement_candidate":
                repaired_records.append({
                    "schema_version": "rxn2-phase2-local-quote-repair-v1",
                    "example_id": legacy["example_id"],
                    "publication_number": legacy["publication_number"],
                    "text_sha256": legacy["text_sha256"],
                    "source_artifact_sha256": legacy["source_artifact_sha256"],
                    "reviews": output_roles, "quote_repairs": changes,
                    "local_validation_failures": failures, "consensus": consensus,
                    "review_status": "needs_human_review",
                    "automatic_acceptance": False,
                })
    content = "".join(json.dumps(item, sort_keys=True) + "\n" for item in repaired_records)
    write_atomic(args.output, content)
    manifest = {
        "schema_version": "rxn2-phase2-local-quote-repair-manifest-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "records_with_repairs_or_exact_agreement": len(repaired_records),
        "all_two_review_record_statuses": dict(sorted(statuses.items())),
        "output_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "database_mutated": False, "automatic_acceptance": False,
    }
    write_atomic(args.manifest, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
