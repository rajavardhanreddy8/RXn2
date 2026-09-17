"""Make the Phase 1 source-evaluation export safe against patent-family leakage.

The input contains only human-text-adjudicated source labels. This script adds
family provenance, removes records whose family cannot be established, and
assigns connected family components to one split. It never promotes chemistry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data/processed/evaluation/phase1-source-evaluation.jsonl"
DEFAULT_DB = ROOT / "data/curated/rxn2-production.sqlite"
DEFAULT_OUTPUT = ROOT / "data/processed/evaluation/phase1-source-evaluation-family-safe.jsonl"
DEFAULT_REPORT = ROOT / "data/processed/evaluation/phase1-source-evaluation-family-safe.manifest.json"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def family_memberships(db_path: Path) -> dict[str, set[str]]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT publication_number,family_id FROM patent_family_member"
        ).fetchall()
    memberships: dict[str, set[str]] = {}
    for publication, family_id in rows:
        memberships.setdefault(publication, set()).add(str(family_id))
    return memberships


def family_components(examples: list[dict]) -> dict[str, str]:
    parent = {item["example_id"]: item["example_id"] for item in examples}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def join(left: str, right: str) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    first_by_family: dict[str, str] = {}
    for item in examples:
        for family_id in item["patent"]["family_ids"]:
            join(first_by_family.setdefault(family_id, item["example_id"]), item["example_id"])
    groups: dict[str, list[str]] = {}
    for item in examples:
        groups.setdefault(find(item["example_id"]), []).append(item["example_id"])
    return {
        example_id: "family-component:" + hashlib.sha256(
            "|".join(sorted(member_ids)).encode()
        ).hexdigest()[:24]
        for member_ids in groups.values()
        for example_id in member_ids
    }


def split(component: str, seed: str) -> str:
    value = int(hashlib.sha256(f"{seed}|{component}".encode()).hexdigest()[:13], 16)
    return "validation" if value % 5 == 0 else "train"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(text, encoding="utf-8", newline="\n")
    partial.replace(path)


def finalize(input_path: Path, db_path: Path, output_path: Path, report_path: Path, seed: str) -> dict:
    memberships = family_memberships(db_path)
    rejected = Counter()
    examples = []
    for record in load_jsonl(input_path):
        family_ids = sorted(memberships.get(record["patent"]["publication_number"], set()))
        if not family_ids:
            rejected["missing_patent_family"] += 1
            continue
        record["patent"]["family_ids"] = family_ids
        examples.append(record)
    if not examples:
        raise ValueError("no evaluation records have patent-family provenance")
    if len({record["example_id"] for record in examples}) != len(examples):
        raise ValueError("duplicate evaluation example ID")

    components = family_components(examples)
    split_by_family: dict[str, str] = {}
    for record in examples:
        component = components[record["example_id"]]
        record["split_group"] = component
        record["split"] = split(component, seed)
        for family_id in record["patent"]["family_ids"]:
            previous = split_by_family.setdefault(family_id, record["split"])
            if previous != record["split"]:
                raise ValueError(f"patent-family leakage: {family_id}")
        for material in record["labels"]["materials"]:
            if material["evidence_quote"] not in record["evidence"]["text"]:
                raise ValueError(f"material quotation lost: {record['example_id']}")
        for fact in record["labels"]["facts"]:
            if fact["evidence_quote"] not in record["evidence"]["text"]:
                raise ValueError(f"fact quotation lost: {record['example_id']}")

    text = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in examples)
    write_atomic(output_path, text)
    report = {
        "schema_version": "rxn2-phase1-family-safe-evaluation-manifest-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "policy": "human-text-adjudicated-source-labels-only",
        "chemical_acceptance": "none",
        "input_examples": len(examples) + sum(rejected.values()),
        "exported_examples": len(examples),
        "excluded": dict(sorted(rejected.items())),
        "split_counts": dict(sorted(Counter(record["split"] for record in examples).items())),
        "family_count": len(split_by_family),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    write_atomic(report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--seed", default="rxn2-phase1-family-safe-v1")
    args = parser.parse_args()
    print(json.dumps(finalize(args.input, args.db, args.output, args.report, args.seed), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
