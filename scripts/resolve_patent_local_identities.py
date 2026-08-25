"""Resolve an unresolved material only from a unique same-publication identity."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import db
from apps.api.app.graph_projection import rebuild_graph_projection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/curated/rxn2-provisional.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/audits/patent-local-identity-application.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(); db.DB_PATH = args.db.resolve()
    report = {"generated_at": datetime.now(UTC).isoformat(), "matches": [], "applied_relation_count": 0,
              "automatic_acceptance": False}
    with db.transaction() as connection:
        rows = connection.execute(
            """SELECT r.relation_candidate_id,r.evidence_span_id,r.predicate,
                      CASE WHEN r.predicate='produced' THEN r.object_text ELSE r.subject_text END material_name,
                      e.publication_number
                 FROM relation_candidate r JOIN evidence_span e USING(evidence_span_id)
                 WHERE r.predicate IN ('consumed','produced') AND r.validation_status='unresolved'
                   AND r.validation_reason='compound_identity_unresolved'"""
        ).fetchall()
        for row in rows:
            candidates = connection.execute(
                """SELECT DISTINCT CASE WHEN r.predicate='produced' THEN r.object_compound_id ELSE r.subject_compound_id END compound_id
                     FROM relation_candidate r JOIN evidence_span e USING(evidence_span_id)
                     WHERE e.publication_number=? AND r.predicate IN ('consumed','produced')
                       AND r.validation_status='validated' AND lower(trim(CASE WHEN r.predicate='produced' THEN r.object_text ELSE r.subject_text END))=lower(trim(?))
                       AND (CASE WHEN r.predicate='produced' THEN r.object_compound_id ELSE r.subject_compound_id END) IS NOT NULL""",
                (row["publication_number"], row["material_name"]),
            ).fetchall()
            if len(candidates) != 1:
                continue
            compound_id = candidates[0]["compound_id"]
            report["matches"].append({"relation_candidate_id": row["relation_candidate_id"], "material_name": row["material_name"], "publication_number": row["publication_number"], "compound_id": compound_id})
            if args.apply:
                field = "object_compound_id" if row["predicate"] == "produced" else "subject_compound_id"
                connection.execute(
                    f"UPDATE relation_candidate SET {field}=?,validation_status='validated',validation_reason='same_publication_unique_resolved_name' WHERE relation_candidate_id=?",
                    (compound_id, row["relation_candidate_id"]),
                )
                report["applied_relation_count"] += 1
    if args.apply: report["projection"] = rebuild_graph_projection()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"matches": len(report["matches"]), "applied_relation_count": report["applied_relation_count"]}))


if __name__ == "__main__": main()
