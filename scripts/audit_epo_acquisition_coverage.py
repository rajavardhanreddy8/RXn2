"""Account for every publication in RXN2's bounded EPO acquisition queue."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "data/processed/manifests"
RAW = ROOT / "data/raw/epo_ops"
DEFAULT_OUTPUT = ROOT / "data/processed/audits/epo-process-patent-acquisition-coverage-20260915.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def publications(manifest: dict) -> set[str]:
    return {item["publication_number"] for item in manifest.get("queued_documents", [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    original = publications(load(MANIFESTS / "process-patent-epo-batch-20260826.json"))
    initial_root = RAW / "process-patent-batch-20260826-complete"
    initial = {path.name for path in initial_root.iterdir() if (path / "manifest.json").is_file()}
    failed_state = load(RAW / "process-patent-retry-20260915/acquisition-agent-state.json")
    failures = {item["publication"]: item["error"] for item in failed_state["results"]}
    replacement_manifest = load(MANIFESTS / "process-patent-epo-family-replacements-20260915.json")
    replacement_reason = {item["publication_number"]: item["reason"] for item in replacement_manifest["queued_documents"]}
    replacement_state = load(RAW / "process-patent-family-replacements-20260915/acquisition-agent-state.json")
    replacement_success = {item["publication"] for item in replacement_state["results"] if item["status"] == "succeeded"}
    # Explicitly documented old -> replacement pairs, not inferred at runtime.
    recovered = {
        "WO-2004065390-A8": "WO-2004065390-A1",
        "WO-2007030721-A9": "WO-2007030721-A2",
        "WO-2008024820-A3": "WO-2008024820-A2",
        "WO-2009084773-A3": "WO-2009084773-A2",
    }
    rows = []
    for publication in sorted(original):
        if publication in initial:
            rows.append({"publication_number": publication, "outcome": "acquired_native_xml", "replacement": None})
        elif publication in recovered and recovered[publication] in replacement_success:
            replacement = recovered[publication]
            rows.append({"publication_number": publication, "outcome": "recovered_by_documented_family_member", "replacement": replacement, "reason": replacement_reason[replacement]})
        elif publication in failures:
            rows.append({"publication_number": publication, "outcome": "public_evidence_unavailable_from_epo_ops", "replacement": None, "reason": failures[publication]})
        else:
            raise ValueError(f"unaccounted publication: {publication}")
    report = {
        "schema_version": "rxn2-epo-bounded-acquisition-coverage-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "original_queue_count": len(original), "outcomes": dict(sorted(Counter(row["outcome"] for row in rows).items())),
        "all_original_publications_accounted_for": len(rows) == len(original), "records": rows,
        "scope": "bounded EPO queue only; not a claim of worldwide patent coverage",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(args.output.suffix + ".partial")
    partial.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(args.output)
    print(json.dumps({key: report[key] for key in report if key != "records"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
