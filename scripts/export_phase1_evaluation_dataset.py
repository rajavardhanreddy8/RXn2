"""Export conservatively human-adjudicated patent examples for Phase 1 evaluation.

This export deliberately creates source-level evaluation labels, not accepted
chemistry or training data. Every row preserves the original patent text,
agent proposal and recorded human selection.
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

# Human verdicts 21–55 chose one entire reviewer output without an open
# chemistry, source-citation, or merge follow-up. The mapping is deliberately
# small; all other reviewed examples remain excluded with a recorded reason.
ADJUDICATED_SELECTIONS = {
    22: "source_completeness",
    23: "source_completeness",
    27: "source_completeness",
    28: "adversarial_chemistry",
    34: "source_completeness",
    35: "adversarial_chemistry",
    39: "adversarial_chemistry",
    43: "source_completeness",
    44: "adversarial_chemistry",
    48: "source_completeness",
    52: "adversarial_chemistry",
    54: "adversarial_chemistry",
}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_proposals(run: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for filename in sorted(glob.glob(str(run / "worker-*/results/agent_review_proposals.jsonl"))):
        for record in load_jsonl(Path(filename)):
            existing = records.setdefault(record["example_id"], record)
            if existing != record:
                raise ValueError(f"conflicting duplicate review proposal: {record['example_id']}")
    return records


def load_conflict_rows(run: Path) -> list[dict]:
    return load_jsonl(run / "human-review-queue.jsonl")


def split(publication: str, seed: str) -> str:
    value = int(hashlib.sha256(f"{seed}|{publication}".encode()).hexdigest()[:13], 16)
    return "validation" if value % 5 == 0 else "train"


def export(run: Path, output: Path, report_path: Path, seed: str) -> dict:
    proposals = load_proposals(run)
    conflicts = load_conflict_rows(run)
    excluded = Counter()
    examples = []
    chosen_ids = set()

    for index, row in enumerate(conflicts, 1):
        source, proposal = row["source"], row["proposal"]
        selection = ADJUDICATED_SELECTIONS.get(index)
        if selection is None:
            excluded["no_single_human_adjudicated_agent_selection"] += 1
            continue
        if proposal["example_id"] not in proposals:
            raise ValueError(f"missing proposal for conflict record {index}")
        if proposal["consensus"]["status"] != "agent_disagreement":
            raise ValueError(f"selected record {index} is not an agent disagreement")
        if any(proposal["local_validation_failures"].values()):
            raise ValueError(f"selected record {index} has local evidence validation failures")
        review = proposal["reviews"].get(selection)
        if not review:
            raise ValueError(f"selected reviewer output absent for conflict record {index}")
        if review["procedure_type"] != "performed":
            raise ValueError(f"selected record {index} is not a performed procedure")
        if review["recommendation"] != "ready_for_human_review":
            raise ValueError(f"selected record {index} is not reviewable")
        if not any(item["role"] == "produced" for item in review["materials"]):
            raise ValueError(f"selected record {index} has no explicit product")
        if not any(item["role"] == "consumed" for item in review["materials"]):
            raise ValueError(f"selected record {index} has no explicit consumed material")
        chosen_ids.add(source["example_id"])
        examples.append({
            "schema_version": "rxn2-phase1-source-evaluation-v1",
            "example_id": source["example_id"],
            "evaluation_status": "human_text_adjudicated_source_label",
            "chemical_acceptance": "not_accepted",
            "human_review_record": {"conflict_index": index, "selected_agent": selection},
            "patent": {
                "publication_number": source["publication_number"],
                "heading": source["heading"],
                "paragraph_labels": source.get("paragraph_labels", []),
            },
            "evidence": {
                "text": source["text"],
                "text_sha256": source["text_sha256"],
                "source_artifact": source["source_artifact"],
                "source_artifact_sha256": source["source_artifact_sha256"],
                "extraction_method": source["extraction_method"],
            },
            "labels": {
                "procedure_type": review["procedure_type"],
                "materials": review["materials"],
                "facts": review["facts"],
                "missing_fields": review["missing_fields"],
                "issues": review["issues"],
                "recommendation": review["recommendation"],
            },
            "split": split(source["publication_number"], seed),
            "split_group": f"publication:{source['publication_number']}",
        })

    if len(examples) != len(ADJUDICATED_SELECTIONS):
        raise ValueError("not every configured adjudication was exported")
    if len(chosen_ids) != len(examples):
        raise ValueError("duplicate selected example ID")
    by_publication = {}
    for item in examples:
        existing = by_publication.setdefault(item["patent"]["publication_number"], item["split"])
        if existing != item["split"]:
            raise ValueError("publication leaked between splits")

    output.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in examples)
    temporary = output.with_suffix(output.suffix + ".partial")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(output)
    report = {
        "schema_version": "rxn2-phase1-evaluation-manifest-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "policy": "human-text-adjudicated-source-labels-only",
        "chemical_acceptance": "none",
        "source_review_records": len(proposals),
        "agent_disagreement_records": len(conflicts),
        "exported_examples": len(examples),
        "excluded_conflict_records": dict(sorted(excluded.items())),
        "split_counts": dict(sorted(Counter(item["split"] for item in examples).items())),
        "source_run_manifest_sha256": sha256(run / "manifest.json"),
        "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "selection_policy": {str(key): value for key, value in ADJUDICATED_SELECTIONS.items()},
    }
    report_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    report_partial = report_path.with_suffix(report_path.suffix + ".partial")
    report_partial.write_text(report_text, encoding="utf-8")
    report_partial.replace(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/evaluation/phase1-source-evaluation.jsonl")
    parser.add_argument("--report", type=Path, default=ROOT / "data/processed/evaluation/phase1-source-evaluation.manifest.json")
    parser.add_argument("--seed", default="rxn2-phase1-family-safe-v1")
    args = parser.parse_args()
    print(json.dumps(export(args.run, args.output, args.report, args.seed), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
