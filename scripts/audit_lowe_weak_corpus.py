"""Stream-audit the immutable Lowe weak-pretraining corpus without promoting it.

The audit deliberately does not parse molecules or infer chemistry. It proves the
frozen JSONL can be read, records its content hash, and reports deterministic
patent-group split counts for pretraining diagnostics only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DRIVE = Path(r"I:\My Drive\RXN2")
DEFAULT_INPUT = DRIVE / "data/processed/lowe_uspto_reactions/2017-06-13/weak-reactions.jsonl"
DEFAULT_SOURCE_MANIFEST = DRIVE / "data/processed/lowe_uspto_reactions/2017-06-13/manifest.json"
DEFAULT_OUTPUT = ROOT / "data/processed/training/lowe-uspto-weak-readiness.json"


def split(patent_number: str | None) -> str:
    # Patent grouping prevents an individual patent from crossing diagnostic splits.
    key = patent_number or "missing-patent"
    value = int(hashlib.sha256(key.encode()).hexdigest()[:12], 16) % 100
    return "train" if value < 80 else "validation" if value < 90 else "test"


def valid_record(item: dict) -> bool:
    reaction = item.get("reaction_smiles")
    return (
        item.get("source_id") == "lowe_uspto_reactions"
        and item.get("source_release_id") == "2017-06-13"
        and item.get("supervision_tier") == "weak_pretraining"
        and item.get("is_synthetic") is False
        and isinstance(reaction, str)
        and reaction.count(">") == 2
    )


def write_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    digest, counts, failures = hashlib.sha256(), Counter(), Counter()
    with args.input.open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            digest.update(raw)
            if not raw.strip():
                failures["blank_line"] += 1
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                failures["invalid_json"] += 1
                continue
            if not valid_record(item):
                failures["invalid_schema_or_reaction_delimiters"] += 1
                continue
            counts["valid_records"] += 1
            counts[f"split:{split(item.get('patent_number'))}"] += 1
            if not item.get("patent_number"):
                counts["missing_patent_number"] += 1
    report = {
        "schema_version": "rxn2-lowe-weak-corpus-readiness-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "input": str(args.input), "input_sha256": digest.hexdigest(),
        "source_manifest_output_sha256": source_manifest.get("output_sha256"),
        "manifest_hash_matches_stream": digest.hexdigest() == source_manifest.get("output_sha256"),
        "records": dict(sorted(counts.items())), "failures": dict(sorted(failures.items())),
        "supervision_tier": "weak_pretraining", "gold_chemistry": False,
        "split_policy": "deterministic patent-number group; diagnostic only",
        "ready_for_final_supervised_training": False,
    }
    write_atomic(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
