#!/usr/bin/env python3
"""Render evidence packets for routes ready for human gold-data curation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "processed" / "review" / "route-review-queue.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "review" / "gold-curation-packets.md"


def load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("review_gate", {}).get("ready_for_gold_curation"):
            records.append(record)
    if not records:
        raise ValueError("no routes are ready for gold curation")
    return sorted(records, key=lambda item: item["review_rank"])


def bullets(items: list[dict], fields: tuple[str, ...]) -> list[str]:
    return ["; ".join(str(item.get(field) or "—") for field in fields) for item in items]


def quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def render(records: list[dict]) -> str:
    lines = [
        "# RXN2 gold-curation packets",
        "",
        "These are review aids, not acceptance records. A reviewer must verify every quoted passage before recording a decision.",
        "",
        "Decision rule: accept only if the patent text supports the performed step, the materials/roles, the normalized mass basis, and the route-target identity. Reject or keep `needs_review` if any point is uncertain.",
        "",
    ]
    for route in records:
        target = route["target"]
        gate = route["review_gate"]
        lines += [
            f"## {route['review_rank']}. {target['name']}",
            "",
            f"- Route: `{route['route_id']}`",
            f"- Target compound: `{target['compound_id']}`",
            f"- Linked drug(s): {', '.join(item['preferred_name'] for item in target['linked_drugs']) or 'none'}",
            f"- Steps: {len(route['steps'])}",
            f"- Gate: performed={gate['all_steps_marked_performed']}; product-mass basis={gate['all_steps_have_normalized_product_mass_g']}; inputs/products={gate['all_reactions_have_consumed_and_produced_participants']}",
            "",
        ]
        for step in route["steps"]:
            reaction_id = step["reaction_id"]
            participants = route["participants_by_reaction"].get(reaction_id, [])
            conditions = route["conditions_by_reaction"].get(reaction_id, [])
            quantities = route["quantities_by_step"].get(step["step_id"], [])
            product_mass = next(
                (
                    item for item in quantities
                    if item["quantity_kind"] == "product_mass" and item["normalized_unit"] == "g"
                ),
                None,
            )
            lines += [
                f"### Step {step['step_order']}: {step['operation_summary']}",
                "",
                f"- Publication: `{step['publication_number']}`",
                f"- Source: {step['source_url']}",
                f"- Product mass: {product_mass['normalized_value']:g} g" if product_mass else "- Product mass: missing",
                f"- Reported yield: {step['yield_percent']:g}%" if step["yield_percent"] is not None else "- Reported yield: not stated",
                f"- Conditions: {', '.join(bullets(conditions, ('condition_type', 'value_text'))) or 'none extracted'}",
                f"- Participants: {', '.join(bullets(participants, ('role', 'compound_name', 'amount_value', 'amount_unit'))) or 'none extracted'}",
                "",
                quote(step["evidence_text"]),
                "",
            ]
        lines += [
            "Reviewer decision: `accept` / `reject` / `needs_review`",
            "Reviewer rationale: _[enter here]_",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    records = load_records(args.input)
    text = render(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(json.dumps({"routes": len(records), "steps": sum(len(item["steps"]) for item in records), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
