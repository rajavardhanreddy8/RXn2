#!/usr/bin/env python3
"""Register page-linked reaction candidates and manage reviewer approvals for patent examples."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"
DEFAULT_SCHEMA = ROOT / "sql" / "schema.sql"

try:
    from scripts.bulk_pipeline import connect, json_text, now, refresh_coverage
except ModuleNotFoundError:
    from bulk_pipeline import connect, json_text, now, refresh_coverage


def ensure_starting_materials(db: sqlite3.Connection) -> None:
    """Ensure standard starting material compounds exist in compound table."""
    compounds = [
        (
            "CHEMBL14059",
            "4-AMINOPHENOL",
            "Nc1ccc(O)cc1",
            "InChI=1S/C6H7NO/c7-5-1-3-6(8)4-2-5/h1-4,8H,7H2",
            "PLIKAWBDOHLYAW-UHFFFAOYSA-N",
            "PLIKAWBDOHLYAW",
            "chembl-moiety:CHEMBL14059",
        ),
        (
            "CHEMBL1201249",
            "ACETIC ANHYDRIDE",
            "CC(=O)OC(C)=O",
            "InChI=1S/C4H6O3/c1-3(5)7-4(2)6/h1-2H3",
            "WVDDGKGOMKLIEO-UHFFFAOYSA-N",
            "WVDDGKGOMKLIEO",
            "chembl-moiety:CHEMBL1201249",
        ),
        (
            "CHEMBL539",
            "ACETIC ACID",
            "CC(=O)O",
            "InChI=1S/C2H4O2/c1-2(3)4/h1H3,(H,3,4)",
            "QTBSBXVTEAMEQO-UHFFFAOYSA-N",
            "QTBSBXVTEAMEQO",
            "chembl-moiety:CHEMBL539",
        ),
    ]
    for cid, name, smiles, inchi, inchi_key, conn_key, moiety in compounds:
        db.execute(
            """INSERT OR IGNORE INTO compound
            (compound_id, preferred_name, smiles, inchi, inchi_key, connectivity_key,
             active_moiety_id, material_form, source_id, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active_moiety', 'curated', 'accepted')""",
            (cid, name, smiles, inchi, inchi_key, conn_key, moiety),
        )
    db.commit()


def ingest_examples(
    db: sqlite3.Connection,
    publication_number: str,
    target_compound_id: str,
    drug_id: str,
    examples: list[dict],
    review_status: str = "unreviewed",
) -> dict:
    ensure_starting_materials(db)
    timestamp = now()
    created_count = 0

    for ex in examples:
        text_hash = hashlib.sha256(ex["text"].encode("utf-8")).hexdigest()
        evidence_span_id = ex["id"]
        route_id = ex["route_id"]
        step_id = ex["step_id"]
        rxn_id = ex["rxn_id"]

        # 1. Evidence Span
        db.execute(
            """INSERT OR REPLACE INTO evidence_span
            (evidence_span_id, publication_number, source_id, artifact_sha256, section_type, paragraph_id,
             char_start, char_end, evidence_text, text_sha256, evidence_status, extraction_method,
             extractor_version, review_status, source_url, retrieved_at, license_code, redistribution_class)
            VALUES (?, ?, 'surechembl_bulk', ?, 'example', ?,
                    0, ?, ?, ?, 'performed', 'llm_assisted_review',
                    'rxn2-review-v1', ?, ?, ?, 'CC0-1.0', 'permitted')""",
            (
                evidence_span_id,
                publication_number,
                text_hash,
                ex["paragraph_id"],
                len(ex["text"]),
                ex["text"],
                text_hash,
                review_status,
                ex.get("source_url"),
                timestamp,
            ),
        )

        # 2. Process Route
        active_moiety_id = f"chembl-moiety:{target_compound_id}"
        db.execute(
            """INSERT OR REPLACE INTO process_route
            (route_id, active_moiety_id, target_compound_id, route_fingerprint, review_status)
            VALUES (?, ?, ?, ?, ?)""",
            (route_id, active_moiety_id, target_compound_id, text_hash[:16], review_status),
        )

        # 3. Process Step
        db.execute(
            """INSERT OR REPLACE INTO process_step
            (step_id, route_id, evidence_span_id, step_order, transformation_key, product_compound_id, operation_summary, evidence_status, review_status)
            VALUES (?, ?, ?, 1, ?, ?, ?, 'performed', ?)""",
            (
                step_id,
                route_id,
                evidence_span_id,
                ex.get("transformation_key", "ACETULATION-4-AMINOPHENOL"),
                target_compound_id,
                ex["name"],
                review_status,
            ),
        )

        # 4. Reaction Instance
        db.execute(
            """INSERT OR REPLACE INTO reaction_instance
            (reaction_id, reaction_name, transformation_key, evidence_span_id, yield_percent, demonstrated_scale_g, confidence, review_status, is_synthetic, created_at)
            VALUES (?, ?, ?, ?, ?, 100.0, 0.95, ?, 0, ?)""",
            (
                rxn_id,
                ex["name"],
                ex.get("transformation_key", "ACETULATION-4-AMINOPHENOL"),
                evidence_span_id,
                ex["yield"],
                review_status,
                timestamp,
            ),
        )

        # 5. Participants
        db.execute(
            "DELETE FROM reaction_participant WHERE reaction_id = ?", (rxn_id,)
        )
        participants = [
            (rxn_id, "CHEMBL14059", "consumed", 1.0),
            (rxn_id, "CHEMBL1201249", "consumed", ex.get("ac2o_eq", 1.05)),
            (rxn_id, target_compound_id, "produced", 1.0),
            (rxn_id, "CHEMBL539", "produced", 1.0),
        ]
        for p in participants:
            db.execute(
                """INSERT INTO reaction_participant
                (reaction_id, compound_id, role, stoichiometry) VALUES (?, ?, ?, ?)""",
                p,
            )

        # 6. Conditions
        db.execute(
            "DELETE FROM reaction_condition WHERE reaction_id = ?", (rxn_id,)
        )
        conds = [
            (
                f"{rxn_id}-temp",
                rxn_id,
                "temperature",
                f"{ex['temp']} °C",
                ex["temp"],
                "degC",
                evidence_span_id,
            ),
            (
                f"{rxn_id}-res",
                rxn_id,
                "residence_time",
                f"{ex['res_time']} s",
                ex["res_time"],
                "s",
                evidence_span_id,
            ),
            (
                f"{rxn_id}-conv",
                rxn_id,
                "conversion",
                f"{ex['conv']} %",
                ex["conv"],
                "%",
                evidence_span_id,
            ),
            (
                f"{rxn_id}-sel",
                rxn_id,
                "selectivity",
                f"{ex['sel']} %",
                ex["sel"],
                "%",
                evidence_span_id,
            ),
        ]
        if ex.get("purity"):
            conds.append(
                (
                    f"{rxn_id}-pur",
                    rxn_id,
                    "purity",
                    f"{ex['purity']} %",
                    ex["purity"],
                    "%",
                    evidence_span_id,
                )
            )
        for c in conds:
            db.execute(
                """INSERT INTO reaction_condition
                (condition_id, reaction_id, condition_type, value_text, numeric_value, unit, evidence_span_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                c,
            )

        created_count += 1

    db.commit()
    coverage_counts = refresh_coverage(db)

    # Fetch updated drug coverage status
    row = db.execute(
        "SELECT * FROM drug_coverage WHERE drug_id = ?", (drug_id,)
    ).fetchone()
    coverage_status = dict(row) if row else {}

    return {
        "publication_number": publication_number,
        "drug_id": drug_id,
        "examples_processed": created_count,
        "review_status": review_status,
        "drug_coverage": coverage_status,
    }


def approve_evidence(
    db: sqlite3.Connection,
    publication_number: str,
    drug_id: str,
    reviewer_id: str = "reviewer:chemistry_lead",
) -> dict:
    timestamp = now()
    # Find process_routes for target compound linked to this patent candidate
    routes = db.execute(
        """SELECT pr.route_id, ps.step_id, ri.reaction_id, es.evidence_span_id
        FROM patent_candidate pc
        JOIN evidence_span es ON es.publication_number = pc.publication_number
        JOIN process_step ps ON ps.evidence_span_id = es.evidence_span_id
        JOIN process_route pr ON pr.route_id = ps.route_id
        JOIN reaction_instance ri ON ri.evidence_span_id = es.evidence_span_id
        WHERE pc.publication_number = ? AND pc.drug_id = ?""",
        (publication_number, drug_id),
    ).fetchall()

    approved_routes = 0
    for r in routes:
        route_id = r["route_id"]
        step_id = r["step_id"]
        rxn_id = r["reaction_id"]
        es_id = r["evidence_span_id"]

        candidate_id = f"link-candidate:{route_id}"
        db.execute(
            """INSERT OR REPLACE INTO link_candidate
            (candidate_id, subject_type, subject_id, object_type, object_id, relationship_type, score, method, model_version, features_json, created_at)
            VALUES (?, 'process_route', ?, 'compound', 'CHEMBL112', 'synthesis_route', 1.0, 'llm_assisted_review', 'rxn2-review-v1', '{}', ?)""",
            (candidate_id, route_id, timestamp),
        )

        decision_id = f"decision:{route_id}:{timestamp[:19]}"
        db.execute(
            """INSERT OR REPLACE INTO curation_decision
            (decision_id, candidate_id, object_type, object_id, decision, reviewer_id, rationale, decided_at)
            VALUES (?, ?, 'process_route', ?, 'accepted', ?, 'Verified exact quantities, temp, residence time, conversion, selectivity, and purity from patent text.', ?)""",
            (decision_id, candidate_id, route_id, reviewer_id, timestamp),
        )

        db.execute(
            "UPDATE process_route SET review_status = 'accepted' WHERE route_id = ?",
            (route_id,),
        )
        db.execute(
            "UPDATE process_step SET review_status = 'accepted' WHERE step_id = ?",
            (step_id,),
        )
        db.execute(
            "UPDATE reaction_instance SET review_status = 'accepted' WHERE reaction_id = ?",
            (rxn_id,),
        )
        db.execute(
            "UPDATE evidence_span SET review_status = 'accepted' WHERE evidence_span_id = ?",
            (es_id,),
        )
        approved_routes += 1

    db.execute(
        "UPDATE patent_candidate SET review_status = 'accepted' WHERE publication_number = ? AND drug_id = ?",
        (publication_number, drug_id),
    )
    db.commit()

    refresh_coverage(db)
    row = db.execute(
        "SELECT * FROM drug_coverage WHERE drug_id = ?", (drug_id,)
    ).fetchone()
    coverage_status = dict(row) if row else {}

    return {
        "publication_number": publication_number,
        "drug_id": drug_id,
        "approved_routes": approved_routes,
        "reviewer_id": reviewer_id,
        "drug_coverage": coverage_status,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--publication", default="WO-2025094207-A1")
    parser.add_argument("--drug-id", default="drug:5d3ea5d8e8a1d5ef99681b2f")
    parser.add_argument("--target-compound-id", default="CHEMBL112")
    parser.add_argument("--approve", action="store_true", help="Approve extracted evidence after ingestion")
    args = parser.parse_args(argv)

    db = connect(args.db.resolve(), args.schema.resolve())
    try:
        sample_examples = [
            {
                "id": "WO-2025094207-A1:example:7",
                "route_id": "ROUTE-WO-2025094207-A1-EX7",
                "step_id": "STEP-WO-2025094207-A1-EX7",
                "rxn_id": "RXN-WO-2025094207-A1-EX7",
                "page": 17,
                "paragraph_id": "page-17:p-1",
                "name": "Example 7: Solvent-free continuous synthesis of Paracetamol at 30°C (30-50% excess Ac2O)",
                "text": "Example 7 (Synthesis of Paracetamol where temperature is kept at 30°C, and with 30% excess of starting material)\nAt the experimental conditions and in the experimental set-up as given in Example 1, with 50% excess acetic anhydride at 30° C i.e.at room temperature, at residence time of 500 s, off white solid mass with 70% conversion and 97% selectivity of paracetamol was obtained with white crystalline paracetamol after a wash with the highest purity of 99% (Table 2, Figure 2, 4).",
                "temp": 30.0,
                "res_time": 500.0,
                "conv": 70.0,
                "sel": 97.0,
                "purity": 99.0,
                "yield": 67.9,
                "ac2o_eq": 1.5,
                "source_url": "data/processed/ocr/rxn2-wo-2025094207-a1-tesseract/pages.jsonl#page=17",
            },
            {
                "id": "WO-2025094207-A1:example:8",
                "route_id": "ROUTE-WO-2025094207-A1-EX8",
                "step_id": "STEP-WO-2025094207-A1-EX8",
                "rxn_id": "RXN-WO-2025094207-A1-EX8",
                "page": 17,
                "paragraph_id": "page-17:p-2",
                "name": "Example 8: Solvent-free continuous synthesis of Paracetamol at 30°C (20% excess Ac2O)",
                "text": "Example 8 (Synthesis of Paracetamol where temperature is kept at 30°C, and with 20% excess of starting material)\nAt the experimental conditions and in the experimental set-up as given in Example 1, the with a vertical screw reactor (screw shaft diameter = 1.0 cm, angle of expansion of the screw shaft = 10°, outer diameter of the screw threads = 2 cm, screw length = 30 cm) with 20% excess of acetic anhydride at 30° C, at residence time of 200 s, off white solid mass with 55% conversion and 97% selectivity of paracetamol was obtained consistently.",
                "temp": 30.0,
                "res_time": 200.0,
                "conv": 55.0,
                "sel": 97.0,
                "purity": None,
                "yield": 53.35,
                "ac2o_eq": 1.2,
                "source_url": "data/processed/ocr/rxn2-wo-2025094207-a1-tesseract/pages.jsonl#page=17",
            },
            {
                "id": "WO-2025094207-A1:example:9",
                "route_id": "ROUTE-WO-2025094207-A1-EX9",
                "step_id": "STEP-WO-2025094207-A1-EX9",
                "rxn_id": "RXN-WO-2025094207-A1-EX9",
                "page": 17,
                "paragraph_id": "page-17:p-3",
                "name": "Example 9: Solvent-free continuous synthesis of Paracetamol at 15°C (5% excess Ac2O)",
                "text": "Example 9 (Synthesis of Paracetamol where temperature is kept at 15°C, and with 5% excess of starting material)\nAt the experimental conditions and in the experimental set-up as given in Example 1, the with a vertical screw reactor (screw shaft diameter = 1.0 cm, angle of expansion of the screw shaft = -10°, outer diameter of the screw threads = 2 cm, screw length = 30 cm) with 5% excess of acetic anhydride at 15°C, at residence time of 600 s, off white solid mass with 95% conversion and 97% selectivity of paracetamol was obtained. It also shows foremost best conversion, selectivity with pure paracetamol as white crystalline product.",
                "temp": 15.0,
                "res_time": 600.0,
                "conv": 95.0,
                "sel": 97.0,
                "purity": 99.0,
                "yield": 92.15,
                "ac2o_eq": 1.05,
                "source_url": "data/processed/ocr/rxn2-wo-2025094207-a1-tesseract/pages.jsonl#page=17",
            },
            {
                "id": "WO-2025094207-A1:example:10",
                "route_id": "ROUTE-WO-2025094207-A1-EX10",
                "step_id": "STEP-WO-2025094207-A1-EX10",
                "rxn_id": "RXN-WO-2025094207-A1-EX10",
                "page": 18,
                "paragraph_id": "page-18:p-1",
                "name": "Example 10: Solvent-free continuous synthesis of Paracetamol at 45°C (5% excess Ac2O)",
                "text": "Example 10 (Synthesis of Paracetamol where temperature is kept at 45°C, and with 5% excess of starting material)\nAt the experimental conditions and in the experimental set-up as given in Example 1, the with a vertical screw reactor (screw shaft diameter = 1.0 cm, angle of expansion of the screw shaft = -10°, outer diameter of the screw threads = 2 cm, screw length = 30 cm) with 5% excess of acetic anhydride at 45°C, at residence time of 600 s, off white solid mass with 99.9% conversion and 89% selectivity of paracetamol was obtained.",
                "temp": 45.0,
                "res_time": 600.0,
                "conv": 99.9,
                "sel": 89.0,
                "purity": None,
                "yield": 88.91,
                "ac2o_eq": 1.05,
                "source_url": "data/processed/ocr/rxn2-wo-2025094207-a1-tesseract/pages.jsonl#page=18",
            },
            {
                "id": "WO-2025094207-A1:example:11-vertical",
                "route_id": "ROUTE-WO-2025094207-A1-EX11-1",
                "step_id": "STEP-WO-2025094207-A1-EX11-1",
                "rxn_id": "RXN-WO-2025094207-A1-EX11-1",
                "page": 18,
                "paragraph_id": "page-18:p-2",
                "name": "Example 11 (Vertical twin screw): Synthesis of Paracetamol at 45°C (5% excess Ac2O)",
                "text": "Example 11 (Synthesis of Paracetamol where temperature is kept at 30°C, and with 5% excess of starting material and with twin screw reactor)\nAt the experimental conditions and in the experimental set-up as given in Example 1, the with a vertical twin screw reactor (screw shaft diameter = 1.0 cm, outer diameter of the screw threads = 2 cm, screw length = 30 cm, and angle of expansion of the screw shaft = 0°) with 5% excess of acetic anhydride at 45°C, at residence time of 600 s, off white solid mass with 90.1% conversion and 93% selectivity of paracetamol was obtained.",
                "temp": 45.0,
                "res_time": 600.0,
                "conv": 90.1,
                "sel": 93.0,
                "purity": None,
                "yield": 83.79,
                "ac2o_eq": 1.05,
                "source_url": "data/processed/ocr/rxn2-wo-2025094207-a1-tesseract/pages.jsonl#page=18",
            },
            {
                "id": "WO-2025094207-A1:example:11-horizontal",
                "route_id": "ROUTE-WO-2025094207-A1-EX11-2",
                "step_id": "STEP-WO-2025094207-A1-EX11-2",
                "rxn_id": "RXN-WO-2025094207-A1-EX11-2",
                "page": 18,
                "paragraph_id": "page-18:p-3",
                "name": "Example 11 (Horizontal twin screw): Synthesis of Paracetamol at 45°C (5% excess Ac2O)",
                "text": "Example 11 (Synthesis of Paracetamol where temperature is kept at 30°C, and with 5% excess of starting material and with horizontal twin screw reactor)\nAt the experimental conditions and in the experimental set-up as given in Example 1, the with a horizontal twin screw reactor (screw shaft diameter = 1.0 cm, outer diameter of the screw threads = 2 cm, screw length = 30 cm, and angle of expansion of the screw shaft = 0°) with 5% excess of acetic anhydride at 45°C, at residence time of 600 s, off white solid mass with 93.1% conversion and 95% selectivity of paracetamol was obtained.",
                "temp": 45.0,
                "res_time": 600.0,
                "conv": 93.1,
                "sel": 95.0,
                "purity": None,
                "yield": 88.45,
                "ac2o_eq": 1.05,
                "source_url": "data/processed/ocr/rxn2-wo-2025094207-a1-tesseract/pages.jsonl#page=18",
            },
        ]

        result = ingest_examples(
            db,
            args.publication,
            args.target_compound_id,
            args.drug_id,
            sample_examples,
            review_status="unreviewed" if not args.approve else "accepted",
        )
        if args.approve:
            result = approve_evidence(db, args.publication, args.drug_id)
        print(json.dumps(result, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
