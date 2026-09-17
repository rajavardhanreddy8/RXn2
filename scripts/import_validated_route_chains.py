"""Import derived, atom-mapped route chains as review-gated process routes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app import db as db_module
from apps.api.app.graph_projection import rebuild_graph_projection


def stable(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/curated/rxn2-production.sqlite")
    parser.add_argument("--input", type=Path, default=ROOT / "data/processed/audits/validated-route-chains.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/audits/validated-route-chain-import.json")
    parser.add_argument("--apply", action="store_true", help="Write review-gated routes and rebuild the derived graph.")
    args = parser.parse_args()
    db_module.DB_PATH = args.db.resolve()
    report = {"generated_at": datetime.now(UTC).isoformat(), "automatic_acceptance": False, "routes": []}
    payload = json.loads(args.input.read_text(encoding="utf-8"))

    with db_module.transaction() as connection:
        for candidate in payload["chains"]:
            route_id = "derived-route:" + stable(candidate["route_candidate_id"])
            target = connection.execute(
                "SELECT active_moiety_id FROM compound WHERE compound_id=?", (candidate["target_compound_id"],)
            ).fetchone()
            if not target:
                raise ValueError(f"unknown target compound: {candidate['target_compound_id']}")
            for reaction_id, evidence_span_id in zip(candidate["reaction_ids"], candidate["evidence_span_ids"]):
                mapped = connection.execute(
                    """SELECT 1 FROM reaction_atom_mapping
                         WHERE reaction_id=? AND validation_status='validated'""", (reaction_id,)
                ).fetchone()
                evidence = connection.execute(
                    "SELECT 1 FROM evidence_span WHERE evidence_span_id=?", (evidence_span_id,)
                ).fetchone()
                if not mapped or not evidence:
                    raise ValueError(f"route source no longer validates: {reaction_id}")
            fingerprint = hashlib.sha256(json.dumps(candidate["reaction_ids"], separators=(",", ":")).encode()).hexdigest()
            existing = connection.execute(
                "SELECT route_fingerprint FROM process_route WHERE route_id=?", (route_id,)
            ).fetchone()
            if existing and existing["route_fingerprint"] != fingerprint:
                raise ValueError(f"derived route fingerprint conflict: {route_id}")
            state = "would_import"
            if args.apply:
                connection.execute(
                    """INSERT OR IGNORE INTO process_route
                       (route_id,active_moiety_id,target_compound_id,route_fingerprint,review_status)
                       VALUES (?,?,?,?, 'needs_review')""",
                    (route_id, target["active_moiety_id"], candidate["target_compound_id"], fingerprint),
                )
                for order, (reaction_id, evidence_span_id) in enumerate(zip(candidate["reaction_ids"], candidate["evidence_span_ids"]), 1):
                    product = candidate["intermediate_compound_ids"][order - 1] if order < len(candidate["reaction_ids"]) else candidate["target_compound_id"]
                    step_id = "derived-step:" + stable(route_id, str(order), reaction_id)
                    connection.execute(
                        """INSERT OR IGNORE INTO process_step
                           (step_id,route_id,evidence_span_id,step_order,transformation_key,product_compound_id,
                            operation_summary,evidence_status,review_status)
                           VALUES (?,?,?,?,?,?,?, 'performed', 'needs_review')""",
                        (step_id, route_id, evidence_span_id, order, reaction_id, product,
                         "Derived from a validated atom map; chemical route remains needs_review."),
                    )
                state = "imported"
            report["routes"].append({"route_id": route_id, "state": state, **candidate})
    if args.apply:
        report["projection"] = rebuild_graph_projection()
    report["route_count"] = len(report["routes"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"route_count": report["route_count"], "apply": args.apply, "automatic_acceptance": False}, sort_keys=True))


if __name__ == "__main__":
    main()
