"""Resolve safely normalized patent material names to one existing compound.

Only presentation noise is removed (purity adjectives and an explicit trailing
amount). Salts, forms, stereochemistry, and formula labels remain untouched.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app import db as db_module

NOISE = re.compile(r"^(?:(?:crude|purified|pure|crystalline|the)\s+)+", re.I)
TRAILING_AMOUNT = re.compile(r"\s*\(\s*\d+(?:\.\d+)?\s*(?:mg|g|kg|ml|l|mmol|mol)\s*\)\s*$", re.I)
UNSAFE = re.compile(r"\b(?:title compound|formula|intermediate|product of)\b", re.I)


def normalized_name(value: str) -> str | None:
    value = TRAILING_AMOUNT.sub("", NOISE.sub("", value)).strip()
    if not value or UNSAFE.search(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/curated/rxn2-provisional.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/audits/local-name-normalization.json")
    parser.add_argument("--apply", action="store_true", help="Write only unique exact local matches.")
    args = parser.parse_args()
    db_module.DB_PATH = args.db.resolve()
    report = {"generated_at": datetime.now(UTC).isoformat(), "matches": [], "applied_relation_count": 0, "automatic_acceptance": False}

    with db_module.transaction() as connection:
        rows = connection.execute(
            """SELECT relation_candidate_id,predicate,subject_text,object_text
                 FROM relation_candidate
                 WHERE predicate IN ('consumed','produced')
                   AND validation_status='unresolved'
                   AND validation_reason='compound_identity_unresolved'"""
        ).fetchall()
        for row in rows:
            text_field = "object_text" if row["predicate"] == "produced" else "subject_text"
            compound_field = "object_compound_id" if row["predicate"] == "produced" else "subject_compound_id"
            original = row[text_field]
            normalized = normalized_name(original)
            if not normalized or normalized.casefold() == original.strip().casefold():
                continue
            matches = connection.execute(
                """SELECT compound_id FROM compound
                     WHERE lower(trim(preferred_name))=lower(trim(?))
                     UNION
                   SELECT dc.compound_id FROM drug_alias a JOIN drug_compound dc USING(drug_id)
                     WHERE lower(trim(a.alias))=lower(trim(?))""",
                (normalized, normalized),
            ).fetchall()
            compound_ids = sorted({item["compound_id"] for item in matches})
            if len(compound_ids) != 1:
                continue
            report["matches"].append({
                "relation_candidate_id": row["relation_candidate_id"], "predicate": row["predicate"],
                "original_name": original, "normalized_name": normalized, "compound_id": compound_ids[0],
            })
            if args.apply:
                connection.execute(
                    f"""UPDATE relation_candidate SET {compound_field}=?,validation_status='validated',
                        validation_reason='exact_local_name_after_presentation_normalization'
                        WHERE relation_candidate_id=?""",
                    (compound_ids[0], row["relation_candidate_id"]),
                )
                report["applied_relation_count"] += 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"matches": len(report["matches"]), "applied_relation_count": report["applied_relation_count"]}, sort_keys=True))


if __name__ == "__main__":
    main()
