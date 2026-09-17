#!/usr/bin/env python3
"""Create a two-reviewer decision template for gold-ready RXN2 routes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "processed" / "review" / "route-review-queue.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "review" / "route-curation-decisions.template.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    records = []
    for line in args.input.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        route = json.loads(line)
        if not route["review_gate"]["ready_for_gold_curation"]:
            continue
        records.append(
            {
                "route_id": route["route_id"],
                "target_compound_id": route["target"]["compound_id"],
                "target_name": route["target"]["name"],
                "source_publications": sorted({step["publication_number"] for step in route["steps"]}),
                "review_gate": route["review_gate"],
                "reviews": [
                    {"reviewer_id": "", "decision": "needs_review", "rationale": ""},
                    {"reviewer_id": "", "decision": "needs_review", "rationale": ""},
                ],
            }
        )
    if not records:
        raise SystemExit("No routes are ready for gold curation")
    payload = {
        "schema_version": "rxn2-route-curation-decisions-v1",
        "instructions": "Replace both reviewer_id and rationale fields. A route is accepted only when two distinct reviewers independently choose accepted. Do not edit route IDs or gate values.",
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"routes": len(records), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
