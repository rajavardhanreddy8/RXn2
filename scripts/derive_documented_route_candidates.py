"""Derive review-gated, text-ordered route candidates from current atom maps.

This script is deliberately read-only. A shared compound alone is not enough:
each connection must be in the same publication and source artifact, with an
earlier evidence span feeding a later one. Output remains a candidate artifact,
never a process route or accepted chemistry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def current_reactions(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """SELECT r.reaction_id,r.evidence_span_id,e.publication_number,e.artifact_sha256,
                  e.char_start,e.char_end,e.evidence_status,e.review_status evidence_review_status
             FROM reaction_instance r JOIN evidence_span e USING(evidence_span_id)
             WHERE r.review_status <> 'rejected'
               AND e.evidence_status='performed' AND e.review_status <> 'rejected'
             ORDER BY e.publication_number,e.artifact_sha256,e.char_start,r.reaction_id"""
    ).fetchall()
    reactions = []
    for row in rows:
        participants = connection.execute(
            """SELECT rp.role,rp.compound_id,coalesce(cp.standardized_smiles,c.smiles) smiles
                 FROM reaction_participant rp
                 JOIN compound c USING(compound_id)
                 LEFT JOIN compound_property cp USING(compound_id)
                 WHERE rp.reaction_id=? AND rp.role IN ('consumed','produced')
                 ORDER BY rp.role,rp.compound_id""",
            (row["reaction_id"],),
        ).fetchall()
        consumed = [dict(item) for item in participants if item["role"] == "consumed" and item["smiles"]]
        produced = [dict(item) for item in participants if item["role"] == "produced" and item["smiles"]]
        if not consumed or len(produced) != 1:
            continue
        reaction_smiles = f"{'.'.join(sorted(item['smiles'] for item in consumed))}>>{produced[0]['smiles']}"
        input_sha256 = hashlib.sha256(reaction_smiles.encode()).hexdigest()
        mapping = connection.execute(
            """SELECT mapping_id FROM reaction_atom_mapping
                 WHERE reaction_id=? AND input_sha256=? AND validation_status='validated'
                 ORDER BY created_at DESC LIMIT 1""",
            (row["reaction_id"], input_sha256),
        ).fetchone()
        if not mapping:
            continue
        reactions.append({
            **dict(row),
            "mapping_id": mapping["mapping_id"],
            "input_sha256": input_sha256,
            "consumed_compound_ids": [item["compound_id"] for item in consumed],
            "product_compound_id": produced[0]["compound_id"],
        })
    return reactions


def derive(reactions: list[dict], max_steps: int) -> list[dict]:
    by_document: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for reaction in reactions:
        by_document[(reaction["publication_number"], reaction["artifact_sha256"])].append(reaction)
    candidates = []
    for (publication, artifact), group in by_document.items():
        successors: dict[str, list[dict]] = defaultdict(list)
        predecessors: set[str] = set()
        for upstream in group:
            for downstream in group:
                if upstream["reaction_id"] == downstream["reaction_id"]:
                    continue
                if upstream["char_end"] > downstream["char_start"]:
                    continue
                if upstream["product_compound_id"] not in downstream["consumed_compound_ids"]:
                    continue
                successors[upstream["reaction_id"]].append(downstream)
                predecessors.add(downstream["reaction_id"])
        by_id = {reaction["reaction_id"]: reaction for reaction in group}

        def emit(path: list[dict]) -> None:
            if len(path) < 2:
                return
            steps = []
            for index, reaction in enumerate(path):
                propagated = path[index - 1]["product_compound_id"] if index else None
                steps.append({
                    "reaction_id": reaction["reaction_id"],
                    "evidence_span_id": reaction["evidence_span_id"],
                    "mapping_id": reaction["mapping_id"],
                    "input_sha256": reaction["input_sha256"],
                    "char_start": reaction["char_start"],
                    "char_end": reaction["char_end"],
                    "consumed_compound_ids": reaction["consumed_compound_ids"],
                    "product_compound_id": reaction["product_compound_id"],
                    "additional_consumed_compound_ids": [
                        compound_id for compound_id in reaction["consumed_compound_ids"]
                        if compound_id != propagated
                    ],
                })
            signature = json.dumps(
                [(step["reaction_id"], step["input_sha256"]) for step in steps],
                separators=(",", ":"),
            )
            candidates.append({
                "route_candidate_id": "documented-route-candidate:" + hashlib.sha256(signature.encode()).hexdigest()[:24],
                "publication_number": publication,
                "source_artifact_sha256": artifact,
                "review_status": "needs_review",
                "acceptance": "not_accepted",
                "reason": "same_artifact_text_order_and_current_validated_atom_map",
                "steps": steps,
            })

        def walk(path: list[dict]) -> None:
            current = path[-1]
            next_steps = [
                item for item in successors[current["reaction_id"]]
                if item["reaction_id"] not in {entry["reaction_id"] for entry in path}
            ]
            if not next_steps or len(path) == max_steps:
                emit(path)
                return
            for next_step in next_steps:
                walk(path + [next_step])

        for reaction_id, reaction in by_id.items():
            if reaction_id not in predecessors:
                walk([reaction])
    return sorted(candidates, key=lambda item: (item["publication_number"], item["route_candidate_id"]))


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/curated/rxn2-production.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/audits/documented-route-candidates.json")
    parser.add_argument("--max-steps", type=int, default=6)
    args = parser.parse_args()
    if args.max_steps < 2:
        raise SystemExit("--max-steps must be at least 2")
    with sqlite3.connect(args.db) as connection:
        connection.row_factory = sqlite3.Row
        reactions = current_reactions(connection)
    candidates = derive(reactions, args.max_steps)
    report = {
        "schema_version": "rxn2-documented-route-candidates-v1",
        "read_only": True,
        "automatic_acceptance": False,
        "current_validated_reactions": len(reactions),
        "route_candidates": len(candidates),
        "candidates": candidates,
    }
    write_atomic(args.output, report)
    print(json.dumps({key: report[key] for key in report if key != "candidates"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
