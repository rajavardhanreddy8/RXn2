"""Import unique PubChem patent-material identities into the provisional DB.

Each imported structure remains unreviewed. This never merges salts/forms into
an approved-drug compound and never changes a material role or route status.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app import db as db_module
from scripts.ingest_performed_route import canonical_compound_id


def record_from(item: dict) -> dict | None:
    properties = item.get("pubchem_properties") or []
    if item.get("pubchem_http_status") != 200 or len(properties) != 1:
        return None
    property_row = properties[0]
    cid, smiles, inchi_key = property_row.get("CID"), property_row.get("SMILES"), property_row.get("InChIKey")
    if not cid or not smiles or not inchi_key:
        return None
    return {
        "compound_id": f"PUBCHEM:{cid}", "pubchem_cid": cid,
        "preferred_name": property_row.get("Title") or item["material_name"],
        "smiles": smiles, "inchi_key": inchi_key,
        "connectivity_key": inchi_key.split("-", 1)[0],
        "molecular_formula": property_row.get("MolecularFormula"),
        "molecular_weight": float(property_row["MolecularWeight"]) if property_row.get("MolecularWeight") else None,
        "material_form": "salt" if "." in smiles else "active_moiety",
        "compound_source_id": "pubchem_bulk", "structure_source": "pubchem_pug_rest",
        "toolkit_name": "source_reported", "toolkit_version": "pubchem-pug-rest",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/curated/rxn2-provisional.sqlite")
    parser.add_argument("--input", type=Path, default=ROOT / "data/processed/enrichment/pubchem-name-candidates.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/audits/pubchem-patent-material-import.json")
    parser.add_argument("--apply", action="store_true", help="Write source-backed identities and exact mention links.")
    args = parser.parse_args()
    db_module.DB_PATH = args.db.resolve()
    items = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = {"generated_at": datetime.now(UTC).isoformat(), "eligible": [], "new_compounds": 0,
              "resolved_relation_count": 0, "automatic_acceptance": False}

    with db_module.transaction() as connection:
        if not connection.execute("SELECT 1 FROM source WHERE source_id='pubchem_bulk'").fetchone():
            raise ValueError("missing required source registry entry: pubchem_bulk")
        for item in items:
            record = record_from(item)
            if record is None:
                continue
            compound_id, inserted = canonical_compound_id(connection, record)
            report["new_compounds"] += int(inserted)
            entry = {
                "material_name": item["material_name"], "pubchem_cid": record["pubchem_cid"],
                "inchi_key": record["inchi_key"], "compound_id": compound_id,
                "inserted": inserted, "query_sha256": item["pubchem_query_sha256"],
            }
            report["eligible"].append(entry)
            if not args.apply:
                continue
            for predicate, text_field, compound_field in (
                ("consumed", "subject_text", "subject_compound_id"),
                ("produced", "object_text", "object_compound_id"),
            ):
                rows = connection.execute(
                    f"""SELECT relation_candidate_id,attributes_json FROM relation_candidate
                         WHERE predicate=? AND lower(trim({text_field}))=lower(trim(?))
                           AND validation_status='unresolved'
                           AND validation_reason='compound_identity_unresolved'""",
                    (predicate, item["material_name"]),
                ).fetchall()
                for row in rows:
                    attributes = json.loads(row["attributes_json"] or "{}")
                    attributes["identity_resolution"] = {
                        "provider": "PubChem PUG REST", "cid": record["pubchem_cid"],
                        "inchi_key": record["inchi_key"], "query_sha256": item["pubchem_query_sha256"],
                        "match": "single_pubchem_property_for_exact_patent_surface_name",
                    }
                    connection.execute(
                        f"""UPDATE relation_candidate SET {compound_field}=?,validation_status='validated',
                            validation_reason='pubchem_unique_exact_patent_material',attributes_json=?
                            WHERE relation_candidate_id=?""",
                        (compound_id, json.dumps(attributes, sort_keys=True), row["relation_candidate_id"]),
                    )
                    report["resolved_relation_count"] += 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"eligible": len(report["eligible"]), "new_compounds": report["new_compounds"],
                      "resolved_relation_count": report["resolved_relation_count"], "apply": args.apply}, sort_keys=True))


if __name__ == "__main__":
    main()
