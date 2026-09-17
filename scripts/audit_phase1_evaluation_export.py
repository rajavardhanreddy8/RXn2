"""Verify that Phase 1 source-evaluation output is evidence-valid and fully accounted."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / "data/processed/review/process-patent-groq-qwen-full-20260831"
DEFAULT_DATASET = ROOT / "data/processed/evaluation/phase1-source-evaluation-family-safe.jsonl"
DEFAULT_DB = ROOT / "data/curated/rxn2-production.sqlite"
DEFAULT_OUTPUT = ROOT / "data/processed/evaluation/phase1-completion-audit.json"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_proposals(run: Path) -> list[dict]:
    records = []
    for path in sorted(glob.glob(str(run / "worker-*/results/agent_review_proposals.jsonl"))):
        records.extend(load_jsonl(Path(path)))
    if len({record["example_id"] for record in records}) != len(records):
        raise ValueError("duplicate review proposal ID")
    return records


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def audit(run: Path, dataset_path: Path, db_path: Path, output_path: Path) -> dict:
    proposals = read_proposals(run)
    dataset = load_jsonl(dataset_path)
    exported_ids = {record["example_id"] for record in dataset}
    if len(exported_ids) != len(dataset):
        raise ValueError("duplicate exported example ID")
    with sqlite3.connect(db_path) as connection:
        family_rows = connection.execute("SELECT publication_number,family_id FROM patent_family_member").fetchall()
    family_by_publication: dict[str, set[str]] = {}
    for publication, family_id in family_rows:
        family_by_publication.setdefault(publication, set()).add(str(family_id))

    split_by_family: dict[str, str] = {}
    for record in dataset:
        text = record["evidence"]["text"]
        if record["chemical_acceptance"] != "not_accepted":
            raise ValueError(f"chemical acceptance leaked into Phase 1: {record['example_id']}")
        if record["labels"]["procedure_type"] != "performed":
            raise ValueError(f"non-performed record exported: {record['example_id']}")
        if not any(item["role"] == "consumed" for item in record["labels"]["materials"]):
            raise ValueError(f"no consumed material: {record['example_id']}")
        if not any(item["role"] == "produced" for item in record["labels"]["materials"]):
            raise ValueError(f"no produced material: {record['example_id']}")
        for item in record["labels"]["materials"] + record["labels"]["facts"]:
            if item["evidence_quote"] not in text:
                raise ValueError(f"quotation does not occur in source: {record['example_id']}")
        expected_families = family_by_publication.get(record["patent"]["publication_number"], set())
        if not expected_families or set(record["patent"]["family_ids"]) != expected_families:
            raise ValueError(f"family provenance mismatch: {record['example_id']}")
        for family_id in expected_families:
            previous = split_by_family.setdefault(family_id, record["split"])
            if previous != record["split"]:
                raise ValueError(f"family split leakage: {family_id}")

    all_ids = {record["example_id"] for record in proposals}
    if not exported_ids <= all_ids:
        raise ValueError("export contains an unknown review record")
    statuses = Counter(record["consensus"]["status"] for record in proposals)
    accounted = {
        "exported": len(exported_ids),
        "reviewed_but_not_exported": len(all_ids - exported_ids),
    }
    if sum(accounted.values()) != len(all_ids):
        raise ValueError("review proposal accounting mismatch")
    report = {
        "schema_version": "rxn2-phase1-completion-audit-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "reviewed_examples": len(proposals),
        "review_consensus_statuses": dict(sorted(statuses.items())),
        "accounting": accounted,
        "dataset_examples": len(dataset),
        "split_counts": dict(sorted(Counter(record["split"] for record in dataset).items())),
        "patent_family_count": len(split_by_family),
        "chemical_acceptance": "none",
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "verified": True,
    }
    write_atomic(output_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(audit(args.run, args.dataset, args.db, args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
