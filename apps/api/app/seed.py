from __future__ import annotations

import json
from datetime import UTC, datetime

from .chemistry import annotate_compound
from .db import transaction


NOW = datetime.now(UTC).isoformat()

COMPOUNDS = [
    ("DEMO-TARGET-1", "Demo benzamide target", "NC(=O)c1ccccc1", 121.139),
    ("DEMO-START-A", "Demo benzoic acid starting material", "O=C(O)c1ccccc1", 122.123),
    ("DEMO-START-B", "Demo benzoyl chloride starting material", "O=C(Cl)c1ccccc1", 140.567),
    ("DEMO-START-C", "Demo nitrogen source", "N", 17.031),
    ("BENCH-ACETAMINOPHEN", "Acetaminophen", None, None),
    ("BENCH-IBUPROFEN", "Ibuprofen", None, None),
    ("BENCH-METFORMIN", "Metformin", None, None),
    ("BENCH-SILDENAFIL", "Sildenafil", None, None),
    ("BENCH-APIXABAN", "Apixaban", None, None),
]

REACTIONS = [
    ("DEMO-RXN-A", "Synthetic fixture route A", "DEMO-AMIDE-A", 84.0, 5000.0, 0.95),
    ("DEMO-RXN-B", "Synthetic fixture route B", "DEMO-AMIDE-B", 91.0, 500.0, 0.90),
]


def seed_demo() -> None:
    """Seed a clearly labelled non-scientific fixture for UI and ranking tests."""
    with transaction() as db:
        db.execute(
            """INSERT OR IGNORE INTO source
            (source_id, name, authority, role, collection_mode, runtime_dependency,
             automated_acquisition_allowed, redistribution, license_code, homepage, registry_json)
            VALUES ('mvp_demo', 'MVP synthetic fixture', 'project', 'testing', 'bundled', 0, 0,
                    'fixture_only', 'CC0-1.0', NULL, ?)""",
            (json.dumps({"warning": "Synthetic fixture; never scientific evidence."}),),
        )
        for compound_id, name, smiles, mw in COMPOUNDS:
            db.execute(
                """INSERT OR IGNORE INTO compound
                (compound_id, preferred_name, smiles, source_id, review_status)
                VALUES (?, ?, ?, 'mvp_demo', 'accepted')""",
                (compound_id, name, smiles),
            )
            if mw:
                annotate_compound(db, compound_id, smiles)
        for compound_id, name, _, _ in COMPOUNDS:
            if not compound_id.startswith(("DEMO-TARGET", "BENCH-")):
                continue
            drug_id = "demo-drug:" + compound_id.casefold()
            db.execute(
                """INSERT OR IGNORE INTO drug_entity
                (drug_id, preferred_name, modality, review_status)
                VALUES (?, ?, 'small_molecule', 'unreviewed')""",
                (drug_id, name),
            )
            db.execute(
                """INSERT OR IGNORE INTO drug_alias
                (drug_id, alias, normalized_alias, alias_type, source_id)
                VALUES (?, ?, ?, 'preferred_name', 'mvp_demo')""",
                (drug_id, name, name.casefold()),
            )
            db.execute(
                """INSERT OR IGNORE INTO drug_compound
                (drug_id, compound_id, relationship_type, review_status)
                VALUES (?, ?, 'active_moiety', 'unreviewed')""",
                (drug_id, compound_id),
            )
            db.execute(
                """INSERT OR IGNORE INTO drug_coverage
                (drug_id, status, identified, refreshed_at, details_json)
                VALUES (?, 'identified', 1, ?, ?)""",
                (drug_id, NOW, json.dumps({"synthetic_fixture": True})),
            )
        for reaction_id, name, key, yield_percent, scale_g, confidence in REACTIONS:
            db.execute(
                """INSERT OR IGNORE INTO reaction_instance
                (reaction_id, reaction_name, transformation_key, yield_percent,
                 demonstrated_scale_g, confidence, review_status, is_synthetic, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'accepted', 1, ?)""",
                (reaction_id, name, key, yield_percent, scale_g, confidence, NOW),
            )
        participants = [
            ("DEMO-RXN-A", "DEMO-START-A", "consumed", 1.0),
            ("DEMO-RXN-A", "DEMO-START-C", "consumed", 1.1),
            ("DEMO-RXN-A", "DEMO-TARGET-1", "produced", 1.0),
            ("DEMO-RXN-B", "DEMO-START-B", "consumed", 1.0),
            ("DEMO-RXN-B", "DEMO-START-C", "consumed", 1.2),
            ("DEMO-RXN-B", "DEMO-TARGET-1", "produced", 1.0),
        ]
        db.executemany(
            """INSERT OR IGNORE INTO reaction_participant
            (reaction_id, compound_id, role, stoichiometry) VALUES (?, ?, ?, ?)""",
            participants,
        )
        for compound_id in ("DEMO-START-A", "DEMO-START-B", "DEMO-START-C"):
            db.execute(
                """INSERT OR IGNORE INTO material_availability
                (compound_id, is_starting_material, geography, reviewed_at, review_status)
                VALUES (?, 1, 'demo', ?, 'accepted')""",
                (compound_id, NOW),
            )
        db.execute(
            """INSERT OR IGNORE INTO supplier
            (supplier_id, supplier_name, geography, review_status)
            VALUES ('DEMO-SUPPLIER', 'Synthetic quote fixture', 'demo', 'accepted')"""
        )
        quotes = [
            ("DEMO-Q-A", "DEMO-START-A", 1000.0, 18.0),
            ("DEMO-Q-B", "DEMO-START-B", 1000.0, 29.0),
            ("DEMO-Q-C", "DEMO-START-C", 1000.0, 4.0),
        ]
        for quote_id, compound_id, pack_g, price in quotes:
            db.execute(
                """INSERT OR IGNORE INTO material_quote
                (quote_id, compound_id, supplier_id, source_url, observed_at, currency,
                 geography, purity_percent, pack_size_value, pack_size_unit, price,
                 imported_at, raw_record_json, review_status)
                VALUES (?, ?, 'DEMO-SUPPLIER', NULL, '2026-01-01', 'USD', 'demo', 99,
                        ?, 'g', ?, ?, ?, 'accepted')""",
                (quote_id, compound_id, pack_g, price, NOW, json.dumps({"synthetic": True})),
            )
