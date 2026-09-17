"""Derive same-publication multi-step route candidates from validated atom maps.

This is a read-only projection. Every chain remains ``needs_review`` because
atom mapping validates a transformation, not the patent-level route claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app import db as db_module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/curated/rxn2-production.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/audits/validated-route-chains.json")
    parser.add_argument("--max-steps", type=int, default=6)
    args = parser.parse_args()
    db_module.DB_PATH = args.db.resolve()

    with db_module.connect() as connection:
        rows = connection.execute(
            """SELECT r.reaction_id,r.evidence_span_id,e.publication_number
                 FROM reaction_instance r JOIN evidence_span e USING(evidence_span_id)
                 WHERE r.review_status<>'rejected' AND EXISTS (
                    SELECT 1 FROM reaction_atom_mapping m
                     WHERE m.reaction_id=r.reaction_id AND m.validation_status='validated'
                 ) ORDER BY e.publication_number,r.reaction_id"""
        ).fetchall()
        reactions = []
        for row in rows:
            participants = connection.execute(
                """SELECT role,compound_id FROM reaction_participant
                     WHERE reaction_id=? ORDER BY role,compound_id""", (row["reaction_id"],)
            ).fetchall()
            inputs = sorted({item["compound_id"] for item in participants if item["role"] in {"reactant", "consumed"}})
            products = sorted({item["compound_id"] for item in participants if item["role"] in {"product", "produced"}})
            if len(products) == 1 and products[0] not in inputs:
                reactions.append({**dict(row), "inputs": inputs, "product": products[0]})

    starts_by_publication = defaultdict(list)
    predecessors = defaultdict(set)
    for reaction in reactions:
        starts_by_publication[reaction["publication_number"]].append(reaction)
    for publication, group in starts_by_publication.items():
        for upstream in group:
            for downstream in group:
                if upstream["reaction_id"] != downstream["reaction_id"] and upstream["product"] in downstream["inputs"]:
                    predecessors[(publication, downstream["reaction_id"])].add(upstream["reaction_id"])

    chains = []
    for publication, group in starts_by_publication.items():
        successor_map = defaultdict(list)
        for upstream in group:
            for downstream in group:
                if upstream["reaction_id"] != downstream["reaction_id"] and upstream["product"] in downstream["inputs"]:
                    successor_map[upstream["reaction_id"]].append(downstream)
        roots = [row for row in group if not predecessors[(publication, row["reaction_id"])]]

        def walk(path: list[dict]) -> None:
            current = path[-1]
            successors = [item for item in successor_map[current["reaction_id"]] if item["reaction_id"] not in {entry["reaction_id"] for entry in path}]
            if not successors or len(path) >= args.max_steps:
                if len(path) >= 2:
                    payload = [(item["reaction_id"], item["evidence_span_id"], item["product"]) for item in path]
                    chains.append({
                        "route_candidate_id": "derived-route:" + hashlib.sha256(repr(payload).encode()).hexdigest()[:24],
                        "publication_number": publication,
                        "reaction_ids": [item["reaction_id"] for item in path],
                        "evidence_span_ids": [item["evidence_span_id"] for item in path],
                        "start_compound_ids": path[0]["inputs"],
                        "intermediate_compound_ids": [item["product"] for item in path[:-1]],
                        "target_compound_id": path[-1]["product"],
                        "review_status": "needs_review",
                        "reason": "same_publication_exact_compound_continuity_with_validated_atom_maps",
                    })
                return
            for successor in successors:
                walk(path + [successor])

        for root in roots:
            walk([root])

    report = {
        "purpose": "Derived same-publication route candidates only; no route is accepted automatically.",
        "validated_reactions_considered": len(reactions),
        "route_candidates": len(chains),
        "chains": sorted(chains, key=lambda item: (item["publication_number"], item["reaction_ids"])),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "chains"}, sort_keys=True))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
