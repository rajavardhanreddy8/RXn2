#!/usr/bin/env python3
"""Export only reviewed, evidence-backed process steps for model training."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "training" / "reviewed_routes.jsonl"
DEFAULT_REPORT = ROOT / "data" / "processed" / "training" / "readiness.json"
POLICY_VERSION = "reviewed-route-component-split-v1"


def now() -> str:
    return datetime.now(UTC).isoformat()


def rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        """
        SELECT ps.step_id, ps.step_order, ps.transformation_key, ps.operation_summary,
               ps.review_status AS step_status, ps.evidence_status AS step_evidence_status,
               pr.route_id, pr.active_moiety_id, pr.target_compound_id,
               pr.review_status AS route_status,
               es.evidence_span_id, es.publication_number, es.source_id, es.artifact_sha256,
               es.paragraph_id, es.section_type, es.char_start, es.char_end, es.evidence_text,
               es.text_sha256, es.evidence_status, es.extraction_method, es.extractor_version,
               es.retrieved_at, es.license_code, es.redistribution_class, es.source_url,
               es.review_status AS evidence_status_review,
               pd.publication_date, pd.title, pf.family_id,
               c.preferred_name, c.smiles, c.inchi_key, c.material_form,
               ri.reaction_id, ri.yield_percent, ri.demonstrated_scale_g,
               ri.confidence AS reaction_confidence, ri.review_status AS reaction_status,
               ri.is_synthetic,
               sl.scale_band, sl.basis_kind, sl.basis_value_g, sl.confidence AS scale_confidence,
               sl.review_status AS scale_status,
               cd.decision AS route_decision, cd.reviewer_id, cd.rationale, cd.decided_at
        FROM process_step ps
        JOIN process_route pr ON pr.route_id = ps.route_id
        JOIN evidence_span es ON es.evidence_span_id = ps.evidence_span_id
        JOIN patent_document pd ON pd.publication_number = es.publication_number
        JOIN compound c ON c.compound_id = pr.target_compound_id
        LEFT JOIN patent_family_member pf ON pf.rowid = (
          SELECT rowid FROM patent_family_member
          WHERE publication_number = es.publication_number ORDER BY family_id LIMIT 1
        )
        LEFT JOIN reaction_instance ri ON ri.rowid = (
          SELECT rowid FROM reaction_instance
          WHERE evidence_span_id = es.evidence_span_id
          ORDER BY review_status = 'accepted' DESC, reaction_id LIMIT 1
        )
        LEFT JOIN scale_label sl ON sl.rowid = (
          SELECT rowid FROM scale_label WHERE step_id = ps.step_id
          ORDER BY review_status = 'accepted' DESC, scale_label_id LIMIT 1
        )
        LEFT JOIN curation_decision cd ON cd.rowid = (
          SELECT rowid FROM curation_decision
          WHERE object_type = 'process_route' AND object_id = pr.route_id
          ORDER BY decided_at DESC, decision_id DESC LIMIT 1
        )
        WHERE ps.evidence_status IN ('performed', 'historical')
        ORDER BY ps.step_id
        """
    ).fetchall()


def reaction_mapping(db: sqlite3.Connection, reaction_id: str) -> list[dict] | None:
    items = [dict(item) for item in db.execute(
        """SELECT rp.role, rp.stoichiometry, c.compound_id, c.smiles, c.inchi_key
           FROM reaction_participant rp JOIN compound c ON c.compound_id = rp.compound_id
           WHERE rp.reaction_id = ? ORDER BY rp.role, c.compound_id""", (reaction_id,)
    )]
    if not any(item["role"] == "consumed" for item in items) or not any(item["role"] == "produced" for item in items):
        return None
    return items if all(item["smiles"] and item["inchi_key"] for item in items) else None


def exclusion(row: sqlite3.Row, mapping: list[dict] | None) -> str | None:
    checks = (
        (row["step_status"] != "accepted", "step_not_accepted"),
        (row["route_status"] != "accepted" or row["route_decision"] != "accepted", "route_not_accepted"),
        (row["evidence_status_review"] != "accepted", "evidence_not_accepted"),
        (not row["reaction_id"] or row["reaction_status"] != "accepted", "reaction_not_accepted"),
        (row["is_synthetic"] != 0, "synthetic_reaction"),
        (mapping is None, "reaction_mapping_incomplete"),
        (not row["scale_band"] or row["scale_band"] == "unknown" or row["scale_status"] != "accepted" or not row["basis_value_g"], "missing_mass_basis"),
        (not row["family_id"], "missing_patent_family"),
        (not row["publication_date"], "missing_publication_date"),
        (not row["active_moiety_id"] or not row["smiles"] or not row["inchi_key"], "missing_validated_structure"),
    )
    return next((reason for failed, reason in checks if failed), None)


def example(db: sqlite3.Connection, row: sqlite3.Row, mapping: list[dict]) -> dict:
    conditions = [dict(item) for item in db.execute(
        """SELECT condition_type, value_text, numeric_value, unit FROM reaction_condition
           WHERE reaction_id = ? ORDER BY condition_id""", (row["reaction_id"],)
    )]
    return {
        "schema_version": "1.0.0",
        "example_id": f"reviewed:{row['step_id']}",
        "is_synthetic": False,
        "patent": {"publication_number": row["publication_number"], "family_id": row["family_id"], "publication_date": row["publication_date"], "title": row["title"]},
        "compound": {"compound_id": row["target_compound_id"], "preferred_name": row["preferred_name"], "active_moiety_id": row["active_moiety_id"], "material_form": row["material_form"], "smiles": row["smiles"], "inchi_key": row["inchi_key"]},
        "route": {"route_id": row["route_id"], "step_id": row["step_id"], "step_order": row["step_order"], "transformation_key": row["transformation_key"], "operation_summary": row["operation_summary"]},
        "reaction": {"reaction_id": row["reaction_id"], "participants": mapping, "conditions": conditions},
        "evidence": {"source_id": row["source_id"], "source_artifact_sha256": row["artifact_sha256"], "paragraph_id": row["paragraph_id"], "section_type": row["section_type"], "char_start": row["char_start"], "char_end": row["char_end"], "text": row["evidence_text"], "span_sha256": row["text_sha256"], "status": row["evidence_status"], "extraction_method": row["extraction_method"], "extractor_version": row["extractor_version"], "source_url": row["source_url"], "retrieved_at": row["retrieved_at"], "license": row["license_code"], "redistribution_class": row["redistribution_class"]},
        "quantities": [{"kind": row["basis_kind"] or "product_mass", "value": row["basis_value_g"], "unit": "g", "confidence": row["scale_confidence"]}],
        "outcome": {"yield_percent": row["yield_percent"], "outcome_type": "reported", "confidence": row["reaction_confidence"]},
        "labels": {"scale_band": row["scale_band"], "development_stage": "unknown", "development_stage_basis": None, "confidence": row["scale_confidence"]},
        "linkage": {"match_type": "exact_structure", "confidence": 1},
        "review": {"status": "accepted", "reviewer_id": row["reviewer_id"], "reviewed_at": row["decided_at"], "rationale": row["rationale"]},
    }


def components(examples: list[dict]) -> dict[str, str]:
    parent = {item["example_id"]: item["example_id"] for item in examples}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def join(left: str, right: str) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    for key in ("family_id", "active_moiety_id"):
        first: dict[str, str] = {}
        for item in examples:
            value = item["patent"].get(key) if key == "family_id" else item["compound"][key]
            if value in first:
                join(first[value], item["example_id"])
            else:
                first[value] = item["example_id"]
    grouped: dict[str, list[str]] = {}
    for item in examples:
        grouped.setdefault(find(item["example_id"]), []).append(item["example_id"])
    return {item_id: f"component:{hashlib.sha256('|'.join(sorted(ids)).encode()).hexdigest()[:24]}" for ids in grouped.values() for item_id in ids}


def split(group: str, seed: str) -> str:
    value = int(hashlib.sha256(f"{seed}|{group}".encode()).hexdigest()[:13], 16) / 0x10000000000000
    return "train" if value < 0.8 else "validation" if value < 0.9 else "test"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def export(db: sqlite3.Connection, output: Path, report_path: Path, seed: str = "scaleup-data-v1") -> dict:
    rejected = Counter()
    accepted = []
    for row in rows(db):
        mapping = reaction_mapping(db, row["reaction_id"]) if row["reaction_id"] else None
        reason = exclusion(row, mapping)
        if reason:
            rejected[reason] += 1
        else:
            accepted.append(example(db, row, mapping))
    assignments = []
    if accepted:
        groups = components(accepted)
        assignments = [{"example_id": item["example_id"], "split": split(groups[item["example_id"]], seed), "leakage_group": groups[item["example_id"]]} for item in accepted]
        text = "".join(json.dumps(item, sort_keys=True) + "\n" for item in accepted)
        temporary = output.with_suffix(output.suffix + ".partial")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(output)
    report = {"created_at": now(), "policy_version": POLICY_VERSION, "seed": seed, "candidate_steps": sum(rejected.values()) + len(accepted), "eligible_examples": len(accepted), "excluded": dict(sorted(rejected.items())), "output": str(output) if accepted else None, "split_counts": dict(sorted(Counter(item["split"] for item in assignments).items())), "assignments": assignments}
    write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--seed", default="scaleup-data-v1")
    args = parser.parse_args()
    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    try:
        print(json.dumps(export(db, args.output, args.report, args.seed), indent=2, sort_keys=True))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
