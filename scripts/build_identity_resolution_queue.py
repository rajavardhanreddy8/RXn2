"""Rank unresolved reaction material names by the number of blocked procedures.

The output is a handoff queue for catalogue enrichment (PubChem/UniChem/manual
curation). It contains verbatim patent names and provenance only; it never
chooses a structure or merges an ambiguous name automatically.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import db


NON_CHEMICAL_NAMES = {
    "tablet", "tablets", "capsule", "capsules", "granule", "granules", "cream",
    "product", "the product", "reaction product", "mixture", "composition", "formulation",
}


def queue_lane(name: str) -> tuple[str, str]:
    normalized = " ".join(name.casefold().split())
    if normalized in NON_CHEMICAL_NAMES:
        return "procedure_classification", "generic_nonchemical_surface_text"
    if re.match(r"^\d+(?:\.\d+)?\s*(?:mg|g|kg|ml|l|mmol|mol)\b", normalized):
        return "name_normalization", "quantity_embedded_in_material_name"
    return "structure_enrichment", "named_material_requires_identity_resolution"


def build_queue(limit: int) -> dict:
    with db.connect() as connection:
        rows = connection.execute(
            """WITH eligible_span AS (
                 SELECT e.evidence_span_id
                 FROM evidence_span e
                 WHERE EXISTS (
                   SELECT 1 FROM relation_candidate d WHERE d.evidence_span_id=e.evidence_span_id
                     AND d.predicate='describes' AND d.validation_status='validated'
                 )
                 AND EXISTS (
                   SELECT 1 FROM relation_candidate c WHERE c.evidence_span_id=e.evidence_span_id
                     AND c.predicate='consumed'
                 )
                 AND EXISTS (
                   SELECT 1 FROM relation_candidate p WHERE p.evidence_span_id=e.evidence_span_id
                     AND p.predicate='produced'
                 )
               ), unresolved AS (
                 SELECT evidence_span_id, predicate,
                        CASE WHEN predicate='produced' THEN object_text ELSE subject_text END AS material_name
                 FROM relation_candidate
                 WHERE predicate IN ('consumed','produced')
                   AND validation_status='unresolved'
                   AND validation_reason='compound_identity_unresolved'
               )
               SELECT lower(trim(u.material_name)) normalized_name,u.material_name,u.predicate,
                      count(*) mention_count,count(DISTINCT u.evidence_span_id) procedure_count,
                      count(DISTINCT e.publication_number) patent_count,
                      min(e.publication_number) example_publication,
                      min(e.evidence_span_id) example_evidence_span_id
               FROM unresolved u JOIN eligible_span q USING(evidence_span_id)
                 JOIN evidence_span e USING(evidence_span_id)
               GROUP BY lower(trim(u.material_name)),u.material_name,u.predicate
               ORDER BY procedure_count DESC,mention_count DESC,normalized_name
               LIMIT ?""",
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        lane, reason = queue_lane(row["material_name"])
        items.append(dict(row) | {
            "resolution_state": "unresolved", "queue_lane": lane, "queue_reason": reason,
            "automatic_merge_allowed": False,
            "required_evidence": ["exact source name", "canonical structure", "source identifier or validated structure match"],
        })
    items.sort(key=lambda item: (item["queue_lane"] != "structure_enrichment", -item["procedure_count"], item["normalized_name"]))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": "Rank candidate names for identity resolution; nonchemical and malformed names are separated before enrichment. No automatic structure assignment.",
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/curated/rxn2-provisional.sqlite"))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=Path("data/processed/audits/identity-resolution-queue.json"))
    args = parser.parse_args()
    db.DB_PATH = args.db.resolve()
    report = build_queue(args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"queued": len(report["items"]), "output": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
