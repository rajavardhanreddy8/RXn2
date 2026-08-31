from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app import db as db_module
from apps.api.app.chemistry import screen_atom_conservation, validate_mapped_reaction


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resumably atom-map structure-screened RXN2 reaction instances."
    )
    parser.add_argument(
        "--db", type=Path, default=ROOT / "data/curated/rxn2-production.sqlite"
    )
    parser.add_argument(
        "--runtime", type=Path, default=ROOT / ".cache/rxnmapper-runtime"
    )
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "data/processed/atom-mapping/results.jsonl",
    )
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def reaction_jobs(connection) -> list[dict]:
    jobs = []
    reactions = connection.execute(
        """SELECT reaction_id,evidence_span_id FROM reaction_instance
           WHERE review_status<>'rejected' ORDER BY reaction_id"""
    ).fetchall()
    for reaction in reactions:
        participants = connection.execute(
            """SELECT rp.role,coalesce(cp.standardized_smiles,c.smiles) smiles
               FROM reaction_participant rp JOIN compound c USING(compound_id)
               LEFT JOIN compound_property cp USING(compound_id)
               WHERE rp.reaction_id=?
                 AND rp.role IN ('reactant','consumed','product','produced')
               ORDER BY rp.role,rp.compound_id""",
            (reaction["reaction_id"],),
        ).fetchall()
        consumed = sorted({
            row["smiles"] for row in participants
            if row["role"] in {"reactant", "consumed"} and row["smiles"]
        })
        products = sorted({
            row["smiles"] for row in participants
            if row["role"] in {"product", "produced"} and row["smiles"]
        })
        if len(products) != 1:
            continue
        screen = screen_atom_conservation(consumed, products[0])
        if screen.status != "validated":
            continue
        reaction_smiles = f"{'.'.join(consumed)}>>{products[0]}"
        input_sha256 = hashlib.sha256(reaction_smiles.encode("utf-8")).hexdigest()
        jobs.append({
            "reaction_id": reaction["reaction_id"],
            "evidence_span_id": reaction["evidence_span_id"],
            "reaction_smiles": reaction_smiles,
            "input_sha256": input_sha256,
        })
    return jobs


def persist(connection, job: dict, mapped: str | None, confidence: float | None,
            mapper_version: str, failure: str | None = None) -> dict:
    validation = validate_mapped_reaction(job["reaction_smiles"], mapped)
    if failure:
        validation = type(validation)("unresolved", failure, 0.0, {})
    mapping_id = "atom-map:" + hashlib.sha256(
        f"{job['reaction_id']}|rxnmapper|{mapper_version}|{job['input_sha256']}".encode("utf-8")
    ).hexdigest()[:24]
    created_at = datetime.now(UTC).isoformat()
    connection.execute(
        """INSERT INTO reaction_atom_mapping
           (mapping_id,reaction_id,input_sha256,unmapped_reaction_smiles,
            mapped_reaction_smiles,mapper_name,mapper_version,model_confidence,
            validation_status,validation_reason,product_atom_coverage,
            mapping_details_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(reaction_id,mapper_name,mapper_version,input_sha256) DO UPDATE SET
             mapped_reaction_smiles=excluded.mapped_reaction_smiles,
             model_confidence=excluded.model_confidence,
             validation_status=excluded.validation_status,
             validation_reason=excluded.validation_reason,
             product_atom_coverage=excluded.product_atom_coverage,
             mapping_details_json=excluded.mapping_details_json,
             created_at=excluded.created_at""",
        (
            mapping_id, job["reaction_id"], job["input_sha256"], job["reaction_smiles"],
            mapped, "rxnmapper", mapper_version, confidence,
            validation.status, validation.reason, validation.product_atom_coverage,
            json.dumps(validation.details, sort_keys=True), created_at,
        ),
    )
    return {
        **job, "mapping_id": mapping_id, "mapped_reaction_smiles": mapped,
        "mapper_name": "rxnmapper", "mapper_version": mapper_version,
        "model_confidence": confidence, **validation.as_dict(), "created_at": created_at,
    }


def main() -> None:
    args = arguments()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    runtime = args.runtime.resolve()
    if not (runtime / "rxnmapper").is_dir():
        raise SystemExit(
            "RXNMapper runtime missing. Install requirements-atom-mapping.txt "
            f"into {runtime} with pip --target."
        )
    sys.path.insert(0, str(runtime))
    from rxnmapper import RXNMapper

    mapper_version = importlib.metadata.version("rxnmapper")
    db_module.DB_PATH = args.db.resolve()
    db_module.initialize()
    with db_module.connect() as connection:
        jobs = reaction_jobs(connection)
        completed = {
            (row["reaction_id"], row["input_sha256"]) for row in connection.execute(
                """SELECT reaction_id,input_sha256 FROM reaction_atom_mapping
                   WHERE mapper_name='rxnmapper' AND mapper_version=?""",
                (mapper_version,),
            )
        }
    jobs = [
        job for job in jobs
        if (job["reaction_id"], job["input_sha256"]) not in completed
    ]
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"RXN2 atom mapping: {len(jobs)} pending; {len(completed)} already recorded")
    mapper = RXNMapper()
    results = []
    for start in range(0, len(jobs), args.batch_size):
        batch = jobs[start:start + args.batch_size]
        try:
            mapped_batch = mapper.get_attention_guided_atom_maps(
                [job["reaction_smiles"] for job in batch]
            )
            mapped_results = [
                (item.get("mapped_rxn"), item.get("confidence"), None)
                for item in mapped_batch
            ]
        except Exception as error:
            mapped_results = []
            for job in batch:
                try:
                    item = mapper.get_attention_guided_atom_maps([job["reaction_smiles"]])[0]
                    mapped_results.append((item.get("mapped_rxn"), item.get("confidence"), None))
                except Exception as item_error:
                    mapped_results.append((None, None, f"mapper_error:{type(item_error).__name__}"))
        with db_module.transaction() as connection:
            for job, (mapped, confidence, failure) in zip(batch, mapped_results):
                results.append(persist(
                    connection, job, mapped, confidence, mapper_version, failure
                ))
        print(f"completed {min(start + len(batch), len(jobs))}/{len(jobs)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with db_module.connect() as connection:
        rows = [dict(row) for row in connection.execute(
            """SELECT * FROM reaction_atom_mapping
               WHERE mapper_name='rxnmapper' AND mapper_version=?
               ORDER BY reaction_id""",
            (mapper_version,),
        )]
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    counts = {}
    for row in rows:
        counts[row["validation_status"]] = counts.get(row["validation_status"], 0) + 1
    print(json.dumps({"records": len(rows), "status_counts": counts, "output": str(args.output)}))


if __name__ == "__main__":
    main()
