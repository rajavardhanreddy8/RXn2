#!/usr/bin/env python3
"""Export evidence-backed, non-approving route records for chemistry review."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "review" / "route-review-queue.jsonl"


def rows(connection: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in connection.execute(query, params)]


def route_records(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    routes = rows(
        connection,
        """
        SELECT pr.route_id, pr.active_moiety_id, pr.target_compound_id,
               pr.route_fingerprint, pr.review_status,
               coalesce(am.preferred_name, target.preferred_name, pr.target_compound_id)
                 AS target_name
        FROM process_route pr
        LEFT JOIN active_moiety am USING (active_moiety_id)
        LEFT JOIN compound target ON target.compound_id = pr.target_compound_id
        WHERE pr.review_status IN ('needs_review', 'unreviewed')
        ORDER BY pr.route_id
        """,
    )
    records: list[dict] = []
    for route in routes:
        steps = rows(
            connection,
            """
            SELECT ps.step_id, ps.step_order, ps.transformation_key,
                   ps.product_compound_id, coalesce(product.preferred_name, ps.product_compound_id)
                     AS product_name, ps.operation_summary, ps.evidence_status,
                   ps.review_status, es.publication_number, es.source_url,
                   es.evidence_text, es.license_code, es.redistribution_class,
                   ri.reaction_id, ri.reaction_name, ri.yield_percent,
                   ri.demonstrated_scale_g, ri.confidence, ri.is_synthetic
            FROM process_step ps
            JOIN evidence_span es ON es.evidence_span_id = ps.evidence_span_id
            LEFT JOIN compound product ON product.compound_id = ps.product_compound_id
            LEFT JOIN reaction_instance ri ON ri.evidence_span_id = ps.evidence_span_id
            WHERE ps.route_id = ?
              AND ps.review_status IN ('needs_review', 'unreviewed')
            ORDER BY ps.step_order, ps.step_id, ri.confidence DESC, ri.reaction_id
            """,
            (route["route_id"],),
        )
        reaction_ids = sorted({step["reaction_id"] for step in steps if step["reaction_id"]})
        participants = {
            reaction_id: rows(
                connection,
                """
                SELECT rp.role, rp.compound_id, coalesce(c.preferred_name, rp.compound_id) AS compound_name,
                       rp.stoichiometry, rp.amount_value, rp.amount_unit
                FROM reaction_participant rp
                LEFT JOIN compound c USING (compound_id)
                WHERE rp.reaction_id = ?
                ORDER BY CASE rp.role WHEN 'consumed' THEN 0 WHEN 'produced' THEN 1 ELSE 2 END,
                         rp.compound_id
                """,
                (reaction_id,),
            )
            for reaction_id in reaction_ids
        }
        quantities = {
            step["step_id"]: rows(
                connection,
                """
                SELECT quantity_kind, original_value, original_unit,
                       normalized_value, normalized_unit, material_compound_id,
                       is_range, confidence
                FROM quantity_observation WHERE step_id = ?
                ORDER BY quantity_kind, quantity_id
                """,
                (step["step_id"],),
            )
            for step in steps
        }
        conditions = {
            reaction_id: rows(
                connection,
                """
                SELECT condition_type, value_text, numeric_value, unit
                FROM reaction_condition WHERE reaction_id = ?
                ORDER BY condition_type, condition_id
                """,
                (reaction_id,),
            )
            for reaction_id in reaction_ids
        }
        drug_names = rows(
            connection,
            """
            SELECT DISTINCT d.drug_id, d.preferred_name
            FROM drug_compound dc JOIN drug_entity d USING (drug_id)
            WHERE dc.compound_id = ?
            ORDER BY d.preferred_name
            """,
            (route["target_compound_id"],),
        )
        terminal_links = rows(
            connection,
            """
            SELECT relationship_type, confidence, review_status, evidence_span_id,
                   CASE WHEN subject_compound_id = ? THEN object_compound_id
                        ELSE subject_compound_id END AS linked_compound_id
            FROM compound_relationship
            WHERE subject_compound_id = ? OR object_compound_id = ?
            ORDER BY review_status, confidence DESC, relationship_id
            """,
            (route["target_compound_id"], route["target_compound_id"], route["target_compound_id"]),
        )
        every_step_performed = bool(steps) and all(step["evidence_status"] == "performed" for step in steps)
        reactions_have_scale = bool(reaction_ids) and all(
            step["demonstrated_scale_g"] is not None
            for step in steps
            if step["reaction_id"]
        )
        reactions_have_inputs_and_products = all(
            {item["role"] for item in participants[reaction_id]}.issuperset({"consumed", "produced"})
            for reaction_id in reaction_ids
        )
        steps_have_product_mass = bool(steps) and all(
            any(
                quantity["quantity_kind"] == "product_mass"
                and quantity["normalized_value"] is not None
                and quantity["normalized_unit"] == "g"
                for quantity in quantities[step["step_id"]]
            )
            for step in steps
        )
        identity_link_accepted = any(link["review_status"] == "accepted" for link in terminal_links)
        ready_for_human_chemistry_review = (
            every_step_performed and reactions_have_scale and reactions_have_inputs_and_products and steps_have_product_mass
        )
        ready_for_gold_curation = ready_for_human_chemistry_review and (
            bool(drug_names) or identity_link_accepted
        )
        records.append(
            {
                "route_id": route["route_id"],
                "route_review_status": route["review_status"],
                "target": {
                    "active_moiety_id": route["active_moiety_id"],
                    "compound_id": route["target_compound_id"],
                    "name": route["target_name"],
                    "linked_drugs": drug_names,
                },
                "steps": steps,
                "participants_by_reaction": participants,
                "quantities_by_step": quantities,
                "conditions_by_reaction": conditions,
                "terminal_identity_links": terminal_links,
                "review_gate": {
                    "all_steps_marked_performed": every_step_performed,
                    "all_reactions_have_demonstrated_scale_g": reactions_have_scale,
                    "all_reactions_have_consumed_and_produced_participants": reactions_have_inputs_and_products,
                    "all_steps_have_normalized_product_mass_g": steps_have_product_mass,
                    "terminal_identity_link_accepted": identity_link_accepted,
                    "ready_for_human_chemistry_review": ready_for_human_chemistry_review,
                    "ready_for_gold_curation": ready_for_gold_curation,
                    "automatic_acceptance": False,
                },
                "review_instruction": (
                    "A chemist must verify the quoted source supports identities, roles, quantities or scale, "
                    "conditions, and product before accepting. This export makes no curation decision."
                ),
            }
        )
    records.sort(
        key=lambda record: (
            not record["review_gate"]["ready_for_human_chemistry_review"],
            -len(record["steps"]),
            record["target"]["name"],
            record["route_id"],
        )
    )
    for rank, record in enumerate(records, 1):
        record["review_rank"] = rank
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    partial.replace(path)


def write_summary(path: Path, records: list[dict]) -> None:
    lines = ["# RXN2 route-review queue", "", "No row is accepted automatically.", ""]
    lines += ["| Rank | Target | Route | Steps | Review gate |", "|---:|---|---|---:|---|"]
    for record in records:
        if record["review_gate"]["ready_for_gold_curation"]:
            gate = "ready for gold curation"
        elif record["review_gate"]["ready_for_human_chemistry_review"]:
            gate = "ready for chemistry review"
        else:
            gate = "needs data completion"
        lines.append(
            f"| {record['review_rank']} | {record['target']['name']} | `{record['route_id']}` | "
            f"{len(record['steps'])} | {gate} |"
        )
    lines += ["", "Open the JSONL file for the exact evidence text, source URLs, participants, and conditions.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.db.is_file():
        raise SystemExit(f"Database not found: {args.db}")
    uri = f"{args.db.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        records = route_records(connection)
    write_jsonl(args.output, records)
    summary = args.output.with_suffix(".md")
    write_summary(summary, records)
    ready_for_chemistry = sum(item["review_gate"]["ready_for_human_chemistry_review"] for item in records)
    ready_for_gold = sum(item["review_gate"]["ready_for_gold_curation"] for item in records)
    print(json.dumps({"routes": len(records), "ready_for_human_chemistry_review": ready_for_chemistry, "ready_for_gold_curation": ready_for_gold, "output": str(args.output), "summary": str(summary)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
