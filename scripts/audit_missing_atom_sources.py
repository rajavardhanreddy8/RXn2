"""List evidence-backed reagent candidates for atom-source review.

This is deliberately read-only. A reagent that can supply missing atoms is a
review candidate, not proof that its role should be changed to ``consumed``.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app.chemistry import Chem, screen_atom_conservation
from apps.api.app import db as db_module


def elements(smiles: str) -> dict[str, int]:
    molecule = Chem.MolFromSmiles(smiles) if Chem else None
    return dict(sorted(Counter(atom.GetSymbol() for atom in molecule.GetAtoms()).items())) if molecule else {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/curated/rxn2-production.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/audits/missing-atom-sources.json")
    args = parser.parse_args()
    db_module.DB_PATH = args.db.resolve()

    with db_module.connect() as connection:
        reactions = connection.execute(
            """SELECT r.reaction_id,r.evidence_span_id,e.publication_number,e.paragraph_id,e.evidence_text
                 FROM reaction_instance r LEFT JOIN evidence_span e USING(evidence_span_id)
                 WHERE r.review_status<>'rejected' ORDER BY r.reaction_id"""
        ).fetchall()
        records = []
        for reaction in reactions:
            participants = connection.execute(
                """SELECT rp.role,rp.compound_id,c.preferred_name,
                          coalesce(cp.standardized_smiles,c.smiles) smiles
                     FROM reaction_participant rp JOIN compound c USING(compound_id)
                     LEFT JOIN compound_property cp USING(compound_id)
                     WHERE rp.reaction_id=? ORDER BY rp.role,rp.compound_id""",
                (reaction["reaction_id"],),
            ).fetchall()
            consumed = [row["smiles"] for row in participants if row["role"] in {"reactant", "consumed"} and row["smiles"]]
            products = [row["smiles"] for row in participants if row["role"] in {"product", "produced"} and row["smiles"]]
            if len(products) != 1:
                continue
            screen = screen_atom_conservation(consumed, products[0])
            if screen.reason != "missing_consumed_atom_source":
                continue
            reagents = [row for row in participants if row["role"] == "reagent" and row["smiles"]]
            candidates = []
            for size in range(1, min(3, len(reagents)) + 1):
                for group in itertools.combinations(reagents, size):
                    augmented = screen_atom_conservation(consumed + [row["smiles"] for row in group], products[0])
                    if augmented.status == "validated":
                        candidates.append({
                            "compound_ids": [row["compound_id"] for row in group],
                            "material_names": [row["preferred_name"] for row in group],
                            "elements": [elements(row["smiles"]) for row in group],
                            "candidate_status": "requires_review",
                            "reason": "recorded_reagent_supplies_missing_product_atoms",
                        })
                if candidates:
                    break
            records.append({
                "reaction_id": reaction["reaction_id"],
                "evidence_span_id": reaction["evidence_span_id"],
                "publication_number": reaction["publication_number"],
                "paragraph_id": reaction["paragraph_id"],
                "missing_product_atoms": screen.missing_product_atoms,
                "candidate_atom_sources": candidates,
                "evidence_text": reaction["evidence_text"],
            })

    report = {
        "purpose": "Read-only role-audit queue; never promotes a reagent to consumed or accepts chemistry.",
        "unresolved_reactions": len(records),
        "with_candidate_atom_source": sum(bool(record["candidate_atom_sources"]) for record in records),
        "without_candidate_atom_source": sum(not record["candidate_atom_sources"] for record in records),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "records"}, sort_keys=True))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
