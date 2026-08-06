#!/usr/bin/env python3
"""Build a read-only, evidence-neutral patent acquisition queue."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"
DEFAULT_OUTPUT = (
    ROOT / "data" / "processed" / "pilot" / "pilot_patent_acquisition_queue.jsonl"
)
PILOT_DRUGS = (
    ("drug:5d3ea5d8e8a1d5ef99681b2f", "high-volume analgesic"),
    ("drug:40a6be06689117401ae0504d", "high-volume chiral NSAID"),
    ("drug:d22bb533c045fe74e1ab7b14", "high-volume polar API"),
    ("drug:644e31cb13de30f630e723b1", "modern anticoagulant"),
    ("drug:fd4a34e0eba9c9145e73f4d5", "complex chiral statin"),
    ("drug:011b45a15197c283cf1a2eb4", "stereochemically complex lipid drug"),
    ("drug:042b5428a6a61931cab4afaf", "CNS small molecule"),
    ("drug:05841255fe9e05bf3ebfecbf", "heterocyclic CNS small molecule"),
    ("drug:c96148f2320a012933736509", "oncology small molecule"),
    ("drug:ce805ce26643d78ffc95af98", "legacy chiral anticoagulant"),
)
ROUTE_TERMS = (
    "process",
    "prepar",
    "synth",
    "intermediate",
    "manufactur",
)
TITLE_EXCLUSIONS = (
    "composition",
    "formulation",
    "dosage",
    "tablet",
    "capsule",
    "inject",
    "use of",
    "treat",
    "detection",
    "analysis",
    "assay",
    "sensor",
    "nanoparticle",
)
FIELD_PRIORITY = {
    "clms": 4,
    "claims": 4,
    "desc": 3,
    "description": 3,
    "abst": 2,
    "abstract": 2,
    "ttl": 1,
    "title": 1,
}
COUNTRY_PRIORITY = {"WO": 4, "EP": 3, "US": 2}


def normalize(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def candidate_score(row: sqlite3.Row, aliases: set[str]) -> tuple:
    title = normalize(row["title"])
    padded_title = f" {title} "
    title_match = int(
        any(f" {alias} " in padded_title for alias in aliases if len(alias) >= 5)
    )
    route_hits = sum(term in title for term in ROUTE_TERMS)
    direct_synthesis = int(title_match and any(term in title for term in ("synth", "prepar")))
    negative_hits = sum(term in title for term in TITLE_EXCLUSIONS)
    return (
        title_match,
        direct_synthesis,
        -negative_hits,
        route_hits,
        FIELD_PRIORITY.get(normalize(row["source_field_name"]), 0),
        COUNTRY_PRIORITY.get(row["country_code"], 0),
        row["publication_date"] or "",
        row["publication_number"],
    )


def build_queue(
    db: sqlite3.Connection,
    pilot_drugs: tuple[tuple[str, str], ...] = PILOT_DRUGS,
) -> list[dict]:
    db.row_factory = sqlite3.Row
    drug_ids = [drug_id for drug_id, _ in pilot_drugs]
    marks = ",".join("?" for _ in drug_ids)
    drugs = {
        row["drug_id"]: row
        for row in db.execute(
            f"SELECT drug_id, preferred_name FROM drug_entity WHERE drug_id IN ({marks})",
            drug_ids,
        )
    }
    missing = [drug_id for drug_id in drug_ids if drug_id not in drugs]
    if missing:
        raise ValueError(f"pilot drugs missing from catalogue: {', '.join(missing)}")

    aliases = {
        drug_id: {normalize(drugs[drug_id]["preferred_name"])} for drug_id in drug_ids
    }
    for row in db.execute(
        f"SELECT drug_id, alias FROM drug_alias WHERE drug_id IN ({marks})", drug_ids
    ):
        aliases[row["drug_id"]].add(normalize(row["alias"]))

    rows_by_drug: dict[str, list[sqlite3.Row]] = {drug_id: [] for drug_id in drug_ids}
    for row in db.execute(
        f"""
        SELECT pc.*, pd.title, pd.publication_date, pd.country_code,
               pfm.family_id, c.inchi_key
        FROM patent_candidate pc
        JOIN patent_document pd USING (publication_number)
        JOIN compound c USING (compound_id)
        LEFT JOIN patent_family_member pfm USING (publication_number)
        WHERE pc.drug_id IN ({marks}) AND pc.review_status <> 'rejected'
        """,
        drug_ids,
    ):
        rows_by_drug[row["drug_id"]].append(row)

    queue = []
    for rank, (drug_id, rationale) in enumerate(pilot_drugs, 1):
        candidates = rows_by_drug[drug_id]
        if not candidates:
            raise ValueError(f"pilot drug has no patent candidates: {drug_id}")
        best_by_family: dict[str, sqlite3.Row] = {}
        for row in candidates:
            family_id = row["family_id"]
            if not family_id or family_id == "surechembl:-1":
                family_id = f"publication:{row['publication_number']}"
            current = best_by_family.get(family_id)
            if current is None or candidate_score(
                row, aliases[drug_id]
            ) > candidate_score(current, aliases[drug_id]):
                best_by_family[family_id] = row
        family_id, selected = max(
            best_by_family.items(),
            key=lambda item: candidate_score(item[1], aliases[drug_id]),
        )
        score = candidate_score(selected, aliases[drug_id])
        queue.append(
            {
                "rank": rank,
                "drug_id": drug_id,
                "drug_name": drugs[drug_id]["preferred_name"],
                "selection_rationale": rationale,
                "compound_id": selected["compound_id"],
                "inchi_key": selected["inchi_key"],
                "family_id": family_id,
                "publication_number": selected["publication_number"],
                "title": selected["title"],
                "publication_date": selected["publication_date"],
                "country_code": selected["country_code"],
                "source_field_name": selected["source_field_name"],
                "match_type": selected["match_type"],
                "confidence": selected["confidence"],
                "candidate_patent_count": len(
                    {row["publication_number"] for row in candidates}
                ),
                "candidate_family_count": len(best_by_family),
                "title_matches_drug": bool(score[0]),
                "direct_synthesis_title": bool(score[1]),
                "negative_title_hits": -score[2],
                "route_keyword_hits": score[3],
                "status": "candidate_only",
                "next_action": "acquire lawful full text",
                "human_review_required": True,
            }
        )
    return queue


def build_batch(db: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Keep the curated diversity seed, then fill it with high-precision titles."""
    if limit < len(PILOT_DRUGS):
        raise ValueError(f"limit must be at least {len(PILOT_DRUGS)}")
    manual = build_queue(db, PILOT_DRUGS)
    manual_ids = {record["drug_id"] for record in manual}
    automatic_drugs = tuple(
        (row["drug_id"], "automatic: direct synthesis-title candidate")
        for row in db.execute(
            """SELECT dc.drug_id FROM drug_coverage dc
               WHERE dc.patents_found=1 AND dc.public_evidence_unavailable=0
               ORDER BY dc.drug_id"""
        )
        if row["drug_id"] not in manual_ids
    )
    automatic = build_queue(db, automatic_drugs)
    eligible = [
        record for record in automatic
        if record["title_matches_drug"]
        and record["direct_synthesis_title"]
        and record["negative_title_hits"] == 0
    ]
    eligible.sort(
        key=lambda record: (
            record["route_keyword_hits"],
            FIELD_PRIORITY.get(normalize(record["source_field_name"]), 0),
            COUNTRY_PRIORITY.get(record["country_code"], 0),
            record["publication_date"] or "",
            record["publication_number"],
        ),
        reverse=True,
    )
    result = manual + eligible[: limit - len(manual)]
    if len(result) != limit:
        raise ValueError(f"only {len(result)} high-precision candidates available for a {limit}-drug batch")
    for rank, record in enumerate(result, 1):
        record["rank"] = rank
    return result


def write_queue(queue: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    partial.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in queue),
        encoding="utf-8",
    )
    partial.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    uri = f"{args.db.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        queue = build_batch(db, args.limit)
    write_queue(queue, args.output)
    print(
        json.dumps(
            {
                "drugs": len(queue),
                "families": len({record["family_id"] for record in queue}),
                "output": str(args.output.resolve()),
                "coverage_changed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
