"""Apply only unambiguous PubChem-to-existing-structure identity matches.

Relations remain needs_review.  This script never creates a compound, changes a
route, or accepts chemistry; it only replaces an unresolved name with the one
existing RXN2 compound that has the exact provider-returned InChIKey.
"""
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

AMBIGUOUS_NAMES = {"salt", "salts", "product", "mixture", "composition", "formulation"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/curated/rxn2-provisional.sqlite"))
    parser.add_argument("--input", type=Path, default=Path("data/processed/enrichment/pubchem-name-candidates.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/audits/pubchem-exact-identity-application.json"))
    parser.add_argument("--apply", action="store_true", help="Required to update the provisional database")
    args = parser.parse_args()
    db.DB_PATH = args.db.resolve()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = {"generated_at": datetime.now(UTC).isoformat(), "eligible": [], "applied_relation_count": 0,
              "automatic_acceptance": False}
    with db.transaction() as connection:
        for item in rows:
            name = item["material_name"]
            properties = item["pubchem_properties"]
            if name.casefold().strip() in AMBIGUOUS_NAMES or len(properties) != 1:
                continue
            property_row = properties[0]
            inchi_key = property_row.get("InChIKey")
            if not inchi_key:
                continue
            matches = connection.execute(
                "SELECT compound_id FROM compound WHERE inchi_key=?", (inchi_key,)
            ).fetchall()
            if len(matches) != 1:
                continue
            compound_id = matches[0]["compound_id"]
            report["eligible"].append({"material_name": name, "compound_id": compound_id,
                                       "pubchem_cid": property_row.get("CID"), "inchi_key": inchi_key})
            if not args.apply:
                continue
            # Apply by actual relation predicate; an intermediate can be both roles.
            for predicate, text_field, compound_field in (
                ("consumed", "subject_text", "subject_compound_id"),
                ("produced", "object_text", "object_compound_id"),
            ):
                candidates = connection.execute(
                    f"""SELECT relation_candidate_id,attributes_json FROM relation_candidate
                        WHERE predicate=? AND lower(trim({text_field}))=lower(trim(?))
                          AND validation_status='unresolved' AND validation_reason='compound_identity_unresolved'""",
                    (predicate, name),
                ).fetchall()
                for candidate in candidates:
                    attributes = json.loads(candidate["attributes_json"] or "{}")
                    attributes["identity_resolution"] = {
                        "provider": "PubChem PUG REST", "cid": property_row.get("CID"),
                        "inchi_key": inchi_key, "query_sha256": item["pubchem_query_sha256"],
                    }
                    connection.execute(
                        f"UPDATE relation_candidate SET {compound_field}=?,validation_status='validated',"
                        "validation_reason='pubchem_exact_name_and_existing_inchikey',attributes_json=? "
                        "WHERE relation_candidate_id=?",
                        (compound_id, json.dumps(attributes, sort_keys=True), candidate["relation_candidate_id"]),
                    )
                    report["applied_relation_count"] += 1
    if args.apply:
        report["projection"] = rebuild_graph_projection()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"eligible": len(report["eligible"]), "applied_relation_count": report["applied_relation_count"]}))


if __name__ == "__main__":
    main()
