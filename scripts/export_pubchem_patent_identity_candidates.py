"""Export review-gated PubChem identity candidates for patent material names.

This script does not write the database. A provider name hit is never enough to
resolve a patent material automatically; it is an auditable candidate that a
later reviewer or exact structure match may confirm.
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


def iter_jsonl(path: Path):
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"malformed JSONL at {path}:{line_number}") from error


def validate_property(item: dict) -> tuple[dict | None, str | None]:
    properties = item.get("pubchem_properties") or []
    if item.get("pubchem_http_status") != 200:
        return None, "provider_not_successful"
    if len(properties) != 1:
        return None, "provider_not_single_match"
    property_row = properties[0]
    cid = property_row.get("CID")
    smiles = property_row.get("SMILES")
    reported_key = property_row.get("InChIKey")
    if not cid or not smiles or not reported_key:
        return None, "provider_missing_structure"
    try:
        from apps.api.app.chemistry import standardize_smiles
        structure = standardize_smiles(smiles)
    except Exception as error:
        return None, f"structure_validation_failed:{type(error).__name__}"
    if structure.inchi_key != reported_key:
        return None, "provider_structure_key_mismatch"
    return {
        "candidate_compound_id": f"PUBCHEM:{cid}",
        "pubchem_cid": cid,
        "provider_title": property_row.get("Title"),
        "standardized_smiles": structure.standardized_smiles,
        "inchi": structure.inchi,
        "inchi_key": structure.inchi_key,
        "molecular_formula": structure.molecular_formula,
        "molecular_weight": structure.molecular_weight,
    }, None


def existing_compounds(connection: sqlite3.Connection, inchi_key: str) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT compound_id FROM compound WHERE inchi_key=? ORDER BY compound_id", (inchi_key,)
    )]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/curated/rxn2-provisional.sqlite")
    parser.add_argument("--input", type=Path, default=ROOT / "data/processed/enrichment/pubchem-name-candidates.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/audits/pubchem-patent-identity-candidates.jsonl")
    parser.add_argument("--report", type=Path, default=ROOT / "data/processed/audits/pubchem-patent-identity-candidates.manifest.json")
    args = parser.parse_args()
    output, rejected = [], Counter()
    with sqlite3.connect(args.db) as connection:
        for item in iter_jsonl(args.input):
            candidate, reason = validate_property(item)
            if reason:
                rejected[reason] += 1
                continue
            exact_matches = existing_compounds(connection, candidate["inchi_key"])
            output.append({
                "schema_version": "rxn2-pubchem-patent-identity-candidate-v1",
                "material_name": item["material_name"],
                "query_sha256": item.get("pubchem_query_sha256"),
                "candidate": candidate,
                "existing_exact_compound_ids": exact_matches,
                "resolution_state": "exact_existing_structure_candidate" if len(exact_matches) == 1 else "unreviewed_name_candidate",
                "automatic_acceptance": False,
                "review_required": True,
            })
    text = "".join(json.dumps(record, sort_keys=True) + "\n" for record in output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(args.output.suffix + ".partial")
    partial.write_text(text, encoding="utf-8")
    partial.replace(args.output)
    manifest = {
        "schema_version": "rxn2-pubchem-patent-identity-candidate-manifest-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "candidate_count": len(output),
        "rejected": dict(sorted(rejected.items())),
        "automatic_acceptance": False,
        "database_mutated": False,
    }
    manifest_path = args.report
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".partial")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
