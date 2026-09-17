"""Atom-map possible reagent atom sources without changing curated roles.

The output is a review artifact. A valid atom map proves only that the
proposed structures can be mapped together; it never promotes a reagent to a
consumed material or creates an accepted route.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app import db as db_module
from apps.api.app.chemistry import screen_atom_conservation, validate_mapped_reaction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/curated/rxn2-production.sqlite")
    parser.add_argument("--audit", type=Path, default=ROOT / "data/processed/audits/missing-atom-sources.json")
    parser.add_argument("--runtime", type=Path, default=ROOT / ".cache/rxnmapper-runtime")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/atom-mapping/candidate-atom-source-mappings.jsonl")
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    if not (runtime / "rxnmapper").is_dir():
        raise SystemExit(f"RXNMapper runtime missing: {runtime}")
    sys.path.insert(0, str(runtime))
    from rxnmapper import RXNMapper

    db_module.DB_PATH = args.db.resolve()
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    mapper = RXNMapper()
    mapper_version = importlib.metadata.version("rxnmapper")
    results = []
    with db_module.connect() as connection:
        for record in audit["records"]:
            if not record["candidate_atom_sources"]:
                continue
            participants = connection.execute(
                """SELECT rp.role,rp.compound_id,coalesce(cp.standardized_smiles,c.smiles) smiles
                     FROM reaction_participant rp JOIN compound c USING(compound_id)
                     LEFT JOIN compound_property cp USING(compound_id)
                     WHERE rp.reaction_id=? ORDER BY rp.role,rp.compound_id""",
                (record["reaction_id"],),
            ).fetchall()
            consumed = [row["smiles"] for row in participants if row["role"] in {"reactant", "consumed"} and row["smiles"]]
            products = [row["smiles"] for row in participants if row["role"] in {"product", "produced"} and row["smiles"]]
            if len(products) != 1:
                continue
            smiles_by_id = {row["compound_id"]: row["smiles"] for row in participants}
            for source in record["candidate_atom_sources"]:
                supplemental = [smiles_by_id[compound_id] for compound_id in source["compound_ids"]]
                reaction_smiles = f"{'.'.join(sorted(consumed + supplemental))}>>{products[0]}"
                mapping = mapper.get_attention_guided_atom_maps([reaction_smiles])[0]
                validation = validate_mapped_reaction(reaction_smiles, mapping.get("mapped_rxn"))
                results.append({
                    "reaction_id": record["reaction_id"],
                    "evidence_span_id": record["evidence_span_id"],
                    "candidate_compound_ids": source["compound_ids"],
                    "candidate_material_names": source["material_names"],
                    "input_sha256": hashlib.sha256(reaction_smiles.encode("utf-8")).hexdigest(),
                    "unmapped_reaction_smiles": reaction_smiles,
                    "mapped_reaction_smiles": mapping.get("mapped_rxn"),
                    "model_confidence": mapping.get("confidence"),
                    "mapper_name": "rxnmapper",
                    "mapper_version": mapper_version,
                    "atom_mapping_validation": validation.as_dict(),
                    "role_assignment_status": "needs_review",
                    "route_status": "unresolved",
                    "reason": "candidate_atom_source_not_promoted_to_consumed",
                })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in results), encoding="utf-8")
    print(json.dumps({"records": len(results), "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
