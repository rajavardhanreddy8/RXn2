"""Produce a deterministic, non-approving readiness report for route candidates.

This does not alter RXN2's curated graph.  It determines whether each performed
procedure has the minimum evidence and resolved structures needed for a later
atom-mapping and chemistry-review pass.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.chemistry import Chem
from apps.api.app import db


def _assessment(consumed: list[str | None], product: str | None) -> tuple[str, str, dict]:
    if not product or not consumed or any(not value for value in consumed):
        return "needs_structure_resolution", "missing_resolved_structure", {}
    if Chem is None:
        return "blocked", "rdkit_unavailable", {}
    try:
        input_molecules = [Chem.MolFromSmiles(value) for value in consumed]
        product_molecule = Chem.MolFromSmiles(product)
        if product_molecule is None or any(value is None for value in input_molecules):
            return "rejected", "unparseable_resolved_structure", {}
        canonical_inputs = {Chem.MolToSmiles(value, canonical=True, isomericSmiles=True) for value in input_molecules}
        canonical_product = Chem.MolToSmiles(product_molecule, canonical=True, isomericSmiles=True)
        if canonical_product in canonical_inputs:
            return "rejected", "self_transformation", {}
        input_elements = Counter(atom.GetSymbol() for molecule in input_molecules for atom in molecule.GetAtoms())
        product_elements = Counter(atom.GetSymbol() for atom in product_molecule.GetAtoms())
        missing_elements = sorted(element for element in product_elements if element not in input_elements)
        return "ready_for_atom_mapping", "resolved_performed_nonself", {
            "consumed_heavy_atoms": sum(input_elements.values()),
            "product_heavy_atoms": sum(product_elements.values()),
            "product_elements_not_in_consumed": missing_elements,
            "note": "Missing product elements may be supplied by a separately extracted reagent; this is a review signal, not a rejection.",
        }
    except Exception:
        return "rejected", "unparseable_resolved_structure", {}


def build_report() -> dict:
    with db.connect() as connection:
        spans = connection.execute(
            """SELECT e.evidence_span_id,e.publication_number,e.paragraph_id,e.source_url
                 FROM evidence_span e
                 WHERE EXISTS (
                   SELECT 1 FROM relation_candidate d WHERE d.evidence_span_id=e.evidence_span_id
                     AND d.predicate='describes' AND d.validation_status='validated'
                 )
                 AND EXISTS (
                   SELECT 1 FROM relation_candidate c WHERE c.evidence_span_id=e.evidence_span_id
                     AND c.predicate='consumed' AND c.validation_status='validated'
                 )
                 AND 1=(
                   SELECT count(DISTINCT p.object_compound_id) FROM relation_candidate p
                   WHERE p.evidence_span_id=e.evidence_span_id AND p.predicate='produced'
                     AND p.validation_status='validated' AND p.object_compound_id IS NOT NULL
                 )
                 ORDER BY e.publication_number,e.evidence_span_id"""
        ).fetchall()
        records = []
        for span in spans:
            consumed = connection.execute(
                """SELECT coalesce(cp.standardized_smiles,c.smiles) smiles FROM relation_candidate r
                    JOIN compound c ON c.compound_id=r.subject_compound_id
                    LEFT JOIN compound_property cp USING(compound_id)
                    WHERE r.evidence_span_id=? AND r.predicate='consumed'
                      AND r.validation_status='validated' ORDER BY r.relation_candidate_id""",
                (span["evidence_span_id"],),
            ).fetchall()
            product = connection.execute(
                """SELECT coalesce(cp.standardized_smiles,c.smiles) smiles FROM relation_candidate r
                    JOIN compound c ON c.compound_id=r.object_compound_id
                    LEFT JOIN compound_property cp USING(compound_id)
                    WHERE r.evidence_span_id=? AND r.predicate='produced'
                      AND r.validation_status='validated' ORDER BY r.relation_candidate_id LIMIT 1""",
                (span["evidence_span_id"],),
            ).fetchone()
            readiness, reason, details = _assessment(
                [row["smiles"] for row in consumed], product["smiles"] if product else None
            )
            records.append({
                "evidence_span_id": span["evidence_span_id"], "publication_number": span["publication_number"],
                "paragraph_id": span["paragraph_id"], "source_url": span["source_url"],
                "consumed_count": len(consumed), "readiness": readiness, "reason": reason, **details,
            })
    counts = Counter(record["readiness"] for record in records)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": "Readiness for atom mapping and chemistry review only; no route is approved by this report.",
        "candidate_procedures": len(records), "counts": dict(sorted(counts.items())), "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/curated/rxn2-provisional.sqlite"),
                        help="Provisional evidence database to audit; this report never mutates it.")
    parser.add_argument("--output", type=Path, default=Path("data/processed/audits/route-readiness.json"))
    args = parser.parse_args()
    db.DB_PATH = args.db.resolve()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("candidate_procedures", "counts")}, sort_keys=True))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
